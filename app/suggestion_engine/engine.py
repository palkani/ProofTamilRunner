"""
Main Suggestion Engine orchestrating all layers.

Implements Layer F: Dedup + Final Ranker
"""

import logging
import time
from typing import List, Dict, Any, Optional
from collections import OrderedDict

from app.suggestion_engine.types import (
    Candidate,
    SuggestionRequest,
    SuggestionResponse,
)
from app.suggestion_engine.normalization import (
    normalize_unicode,
    is_valid_tamil_word,
    extract_tamil_boundary,
    clean_tamil_text,
)
from app.suggestion_engine.layers.core_transliteration import CoreTransliterationLayer
from app.suggestion_engine.layers.tamil_vowel_expand import TamilVowelExpandLayer
from app.suggestion_engine.layers.context_join import ContextJoinLayer
from app.suggestion_engine.layers.frequency_ranker import FrequencyRankerLayer
from app.suggestion_engine.layers.heuristics import HeuristicsLayer
from app.core.cache import LRUCache, make_cache_key
from app.core.config import settings

logger = logging.getLogger(__name__)

# Algorithm version
ALGORITHM_VERSION = "1.0.1"


def _normalize_roman_q(q: str) -> str:
    q = (q or "").lower().strip()
    # keep only a-z and apostrophe (IME romanization)
    import re
    q = re.sub(r"[^a-z']", "", q)
    return q


# Canonical ranked suggestions for extremely common tokens where users expect Google-style results.
# This is a backend quality guarantee.
CANONICAL_RANKED = {
    # function words
    "enna": ["என்ன"],
    "namma": ["நம்ம"],
    "enathu": ["எனது"],
    "enadu": ["எனது"],
    "enadhu": ["எனது"],
    # south
    "therkku": ["தெற்கு"],
    "therku": ["தெற்கு"],
    "therkk": ["தெற்கு"],
    # enpathu family (competitor-style ranking)
    "enpathu": ["என்பது", "எண்பது", "எண்பத்து", "என்பத்து", "எண்பத"],
    # tamil
    "tamil": ["தமிழ்"],
    "thamizh": ["தமிழ்"],
    "thamiz": ["தமிழ்"],
    "tamizh": ["தமிழ்"],
    "tamiz": ["தமிழ்"],
}


class SuggestionEngine:
    """
    Main suggestion engine orchestrating all layers.
    
    Layers:
    - A: Core Transliteration (strict)
    - B: Tamil Vowel Expansion
    - C: Context-Aware Completion
    - D: Frequency Ranking
    - E: Heuristic Neighbors (smart mode only)
    - F: Dedup + Final Ranker
    """

    def __init__(
        self,
        core_cache: Optional[LRUCache] = None,
        final_cache: Optional[LRUCache] = None,
    ):
        """
        Initialize suggestion engine.
        
        Args:
            core_cache: Cache for core transliteration (mode, q) -> candidates
            final_cache: Cache for final suggestions (mode, q, boundary, last_char_class) -> final
        """
        self.core_layer = CoreTransliterationLayer()
        self.vowel_layer = TamilVowelExpandLayer()
        self.context_layer = ContextJoinLayer()
        self.frequency_layer = FrequencyRankerLayer()
        self.heuristics_layer = HeuristicsLayer(enabled=True)

        # Caches
        self.core_cache = core_cache or LRUCache(
            max_size=10000, default_ttl=settings.CACHE_TTL_SECONDS
        )
        self.final_cache = final_cache or LRUCache(
            max_size=10000, default_ttl=settings.CACHE_TTL_SECONDS
        )

    async def suggest(
        self, request: SuggestionRequest, request_id: str = "n/a"
    ) -> SuggestionResponse:
        """
        Generate suggestions using layered algorithm.
        
        Returns SuggestionResponse with suggestions and metadata.
        """
        start_time = time.perf_counter()
        layer_timings: Dict[str, float] = {}
        layers_used: List[str] = []
        cache_hits = {"core": False, "final": False}
        runner_error = False
        runner_error_source = None

        try:
            # Validate inputs
            validation_error = self._validate_request(request)
            if validation_error:
                return SuggestionResponse(
                    success=False,
                    suggestions=[],
                    meta={},
                    error=validation_error,
                )

            # Canonical short-circuit (ranked list). Guarantees correctness for high-signal tokens.
            nq = _normalize_roman_q(request.q)
            forced = CANONICAL_RANKED.get(nq)
            if forced:
                items: List[Candidate] = []
                for idx, w in enumerate(forced[: request.limit]):
                    score = max(0.55, 1.0 - (idx * 0.1))
                    items.append(
                        Candidate(
                            word=w,
                            base_score=round(score, 2) if idx > 0 else 1.0,
                            source_layer="canonical",
                            debug={"canonical": True, "q": nq, "rank": idx + 1},
                        )
                    )
                return self._build_response(
                    items,
                    request,
                    layer_timings,
                    ["CANON"],
                    {"core": False, "final": False},
                    runner_error=False,
                    total_time=time.perf_counter() - start_time,
                )

            # Prepare context info
            left_context, right_context, is_at_boundary = extract_tamil_boundary(
                request.context or "", request.cursor or (len(request.context or ""))
            )
            last_tamil_char_class = None
            if request.context:
                from app.suggestion_engine.normalization import get_last_tamil_char_class
                last_tamil_char_class = get_last_tamil_char_class(left_context)

            # Check final cache
            final_cache_key = make_cache_key(
                request.q,
                request.mode,
                str(is_at_boundary),
                str(last_tamil_char_class or ""),
            )
            cached_final = self.final_cache.get(final_cache_key)
            if cached_final:
                cache_hits["final"] = True
                logger.info(
                    "suggestion_final_cache_hit request_id=%s q=%s",
                    request_id,
                    request.q,
                )
                return self._build_response(
                    cached_final,
                    request,
                    layer_timings,
                    layers_used,
                    cache_hits,
                    runner_error,
                    time.perf_counter() - start_time,
                )

            # Layer A: Core Transliteration
            layer_start = time.perf_counter()
            core_cache_key = make_cache_key(request.q, request.mode)
            cached_core = self.core_cache.get(core_cache_key)
            if cached_core:
                cache_hits["core"] = True
                core_candidates = cached_core
            else:
                # Generate and check for runner errors
                core_candidates, runner_err = await self.core_layer.generate(
                    request.q, request.mode, request_id
                )
                if runner_err:
                    runner_error = True
                    runner_error_source = "core_transliteration"
                if core_candidates:
                    self.core_cache.set(core_cache_key, core_candidates)
            layer_timings["core"] = (time.perf_counter() - layer_start) * 1000
            layers_used.append("A")

            all_candidates: List[Candidate] = list(core_candidates)

            # Layer B: Vowel Expansion (only if core produced results)
            if core_candidates:
                layer_start = time.perf_counter()
                vowel_candidates = self.vowel_layer.generate(request.q, core_candidates)
                all_candidates.extend(vowel_candidates)
                layer_timings["vowel"] = (time.perf_counter() - layer_start) * 1000
                layers_used.append("B")

            # Layer C: Context-Aware Completion
            if request.context is not None:
                layer_start = time.perf_counter()
                all_candidates = self.context_layer.generate(
                    request.q, all_candidates, request.context, request.cursor
                )
                layer_timings["context"] = (time.perf_counter() - layer_start) * 1000
                layers_used.append("C")

            # Layer E: Heuristics (smart mode only)
            if request.mode == "smart":
                layer_start = time.perf_counter()
                heuristic_candidates = self.heuristics_layer.generate(
                    request.q, request.mode
                )
                all_candidates.extend(heuristic_candidates)
                layer_timings["heuristics"] = (
                    time.perf_counter() - layer_start
                ) * 1000
                layers_used.append("E")

            # Layer D: Frequency Ranking
            layer_start = time.perf_counter()
            all_candidates = self.frequency_layer.apply_boost(all_candidates)
            layer_timings["frequency"] = (time.perf_counter() - layer_start) * 1000
            layers_used.append("D")

            # Layer F: Dedup + Final Ranker
            layer_start = time.perf_counter()
            final_candidates = self._dedup_and_rank(all_candidates, request.q)
            layer_timings["final_rank"] = (time.perf_counter() - layer_start) * 1000
            layers_used.append("F")

            # Limit results
            final_candidates = final_candidates[: request.limit]

            # Cache final results
            if final_candidates:
                self.final_cache.set(final_cache_key, final_candidates)

            total_time = time.perf_counter() - start_time

            return self._build_response(
                final_candidates,
                request,
                layer_timings,
                layers_used,
                cache_hits,
                runner_error,
                total_time,
            )

        except Exception as e:
            logger.exception("suggestion_engine_error request_id=%s error=%s", request_id, str(e))
            return SuggestionResponse(
                success=False,
                suggestions=[],
                meta={"error": str(e)},
                error={"code": "INTERNAL_ERROR", "message": str(e)},
            )

    def _dedup_and_rank(
        self, candidates: List[Candidate], q: str
    ) -> List[Candidate]:
        """
        Layer F: Deduplicate and final ranking.
        
        - Normalize and deduplicate by word
        - Apply penalties for unlikely matches
        - Sort by: score desc, word length asc, unicode codepoint asc
        """
        # Deduplicate by normalized word (keep highest score)
        seen: Dict[str, Candidate] = {}
        for cand in candidates:
            # First clean the text to remove formatting characters
            cleaned = clean_tamil_text(cand.word)
            # Then normalize Unicode
            normalized = normalize_unicode(cleaned)
            if not is_valid_tamil_word(normalized):
                continue

            # Apply penalties
            penalty = self._calculate_penalty(normalized, q)
            adjusted_score = max(0.0, cand.base_score - penalty)

            if normalized not in seen or seen[normalized].base_score < adjusted_score:
                seen[normalized] = Candidate(
                    word=normalized,
                    base_score=adjusted_score,
                    source_layer=cand.source_layer,
                    debug=cand.debug,
                )

        # Sort: score desc, length asc, unicode asc
        sorted_candidates = sorted(
            seen.values(),
            key=lambda c: (
                -c.base_score,  # desc
                len(c.word),  # asc
                c.word,  # unicode asc
            ),
        )

        return sorted_candidates

    def _calculate_penalty(self, word: str, q: str) -> float:
        """
        Calculate penalty for unlikely matches.
        
        Penalties:
        - Length mismatch (too long for short input)
        - Invalid joins
        - Rare vowel expansions
        """
        penalty = 0.0

        # Length mismatch: if input is 1 char, prefer 1-2 char words
        if len(q) == 1 and len(word) > 3:
            penalty += 0.05 * (len(word) - 3)

        # Rare vowel expansions (very long for short input)
        if len(q) <= 2 and len(word) > 4:
            penalty += 0.10

        return penalty

    def _validate_request(self, request: SuggestionRequest) -> Optional[Dict[str, Any]]:
        """Validate request parameters."""
        # q length
        if not request.q or len(request.q) < 1:
            return {"code": "INVALID_INPUT", "message": "q must be at least 1 character"}
        if len(request.q) > 40:
            return {"code": "INVALID_INPUT", "message": "q must be at most 40 characters"}

        # limit
        if request.limit < 1 or request.limit > 20:
            return {"code": "INVALID_INPUT", "message": "limit must be between 1 and 20"}

        # mode
        if request.mode not in ("smart", "strict"):
            return {"code": "INVALID_INPUT", "message": "mode must be 'smart' or 'strict'"}

        # context length
        if request.context and len(request.context) > 5000:
            return {
                "code": "INVALID_INPUT",
                "message": "context must be at most 5000 characters",
            }

        # cursor bounds
        if request.context and request.cursor is not None:
            if request.cursor < 0 or request.cursor > len(request.context):
                return {
                    "code": "INVALID_INPUT",
                    "message": "cursor must be within context bounds",
                }

        return None

    def _build_response(
        self,
        candidates: List[Candidate],
        request: SuggestionRequest,
        layer_timings: Dict[str, float],
        layers_used: List[str],
        cache_hits: Dict[str, bool],
        runner_error: bool,
        total_time: float,
    ) -> SuggestionResponse:
        """Build response with suggestions and metadata."""
        suggestions = [c.to_dict() for c in candidates]

        # Normalize scores so top suggestion is 1.0 and others are scaled into 0..1.
        if suggestions:
            try:
                max_score = max(float(s.get("score", 0.0) or 0.0) for s in suggestions) or 0.0
            except Exception:
                max_score = 0.0
            if max_score > 0:
                for i, s in enumerate(suggestions):
                    raw = float(s.get("score", 0.0) or 0.0)
                    scaled = raw / max_score
                    s["score"] = 1.0 if i == 0 else round(max(0.0, min(1.0, scaled)), 2)

        # Build meta
        meta = {
            "algorithm_version": ALGORITHM_VERSION,
            "layers_used": layers_used,
            "timings_ms": {k: round(v, 2) for k, v in layer_timings.items()},
            "total_time_ms": round(total_time * 1000, 2),
            "cache_hits": cache_hits,
            "runner_error": runner_error,
            "mode": request.mode,
            "limit": request.limit,
            "context_present": request.context is not None,
        }

        # Truncate context in logs (security)
        if request.context:
            context_len = len(request.context)
            context_preview = (
                request.context[:12] + "..." if len(request.context) > 12 else request.context
            )
            meta["context_length"] = context_len
            meta["context_preview"] = context_preview

        return SuggestionResponse(
            success=True,
            suggestions=suggestions,
            meta=meta,
        )

