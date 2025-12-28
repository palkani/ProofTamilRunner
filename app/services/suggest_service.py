"""
Production-grade suggest API service implementing 12-step pipeline.
"""

import logging
import math
import time
import unicodedata
from typing import List, Dict, Optional, Tuple

from app.adapters.aksharamukha import AksharaAdapter
from app.core.cache import LRUCache, make_cache_key
from app.core.config import settings
from app.core.freq_dict import freq_score, has_freq
from app.services.tamil_linguistics import (
    normalize_roman_input,
    normalize_unicode,
    validate_tamil_orthography,
    eliminate_morphological_garbage,
)
from app.services.canonical_map import get_canonical

logger = logging.getLogger(__name__)

# Maximum candidates to generate from Aksharamukha
MAX_AKSHARA_CANDIDATES = 50

# Default limit
DEFAULT_LIMIT = 5
MAX_LIMIT = 10


class SuggestService:
    """Production-grade suggest API service."""

    def __init__(self):
        self.adapter = AksharaAdapter()
        self.cache = LRUCache(
            max_size=settings.CACHE_MAX_SIZE, default_ttl=settings.CACHE_TTL_SECONDS
        )

    async def suggest(
        self,
        q: str,
        limit: int = DEFAULT_LIMIT,
        mode: str = "spoken",
        prev: Optional[str] = None,
        request_id: str = "n/a",
    ) -> Tuple[List[Dict[str, any]], Dict[str, any]]:
        """
        Main suggest API implementation following 12-step pipeline.

        Returns: (suggestions, metadata)
        """
        start_time = time.perf_counter()

        # Step 0: Validate input
        if not q or len(q.strip()) == 0:
            return [], {"error": "query required"}

        if len(q) > settings.MAX_TEXT_LEN:
            return [], {"error": f"query too long (max {settings.MAX_TEXT_LEN})"}

        limit = max(1, min(limit or DEFAULT_LIMIT, MAX_LIMIT))

        # Step 10: Caching (check cache first)
        cache_key = make_cache_key("suggest", q, prev or "", str(limit), mode)
        cached = self.cache.get(cache_key)
        if cached:
            logger.info("suggest_cache_hit request_id=%s q=%s", request_id, q)
            return cached, {"cache": "hit"}

        metadata = {
            "cache": "miss",
            "raw_candidate_count": 0,
            "after_rules_count": 0,
            "after_lexicon_count": 0,
            "final_count": 0,
        }

        # Step 1: Roman input normalization
        normalized_input = normalize_roman_input(q)
        input_length = len(normalized_input)

        # Step 2: Canonical override short-circuit
        canonical_output, is_canonical = get_canonical(normalized_input)
        if is_canonical:
            logger.info(
                "suggest_canonical_hit request_id=%s q=%s output=%s",
                request_id,
                q,
                canonical_output,
            )
            result = [
                {
                    "word": canonical_output,
                    "score": 1.0,
                    "source": "canonical",
                }
            ]
            self.cache.set(cache_key, result)
            metadata["final_count"] = 1
            metadata["source"] = "canonical"
            latency_ms = (time.perf_counter() - start_time) * 1000
            metadata["latency_ms"] = latency_ms
            logger.info(
                "suggest_canonical_return request_id=%s q=%s latency_ms=%.2f",
                request_id,
                q,
                latency_ms,
            )
            return result, metadata

        # Step 3: Candidate generation via Aksharamukha
        raw_candidates = []
        try:
            akshara_outputs = await self.adapter.transliterate(normalized_input, mode)
            if akshara_outputs:
                # Aksharamukha adapter returns a list (may be single item)
                if isinstance(akshara_outputs, list):
                    raw_candidates.extend(akshara_outputs[:MAX_AKSHARA_CANDIDATES])
                elif isinstance(akshara_outputs, str):
                    raw_candidates.append(akshara_outputs)
                else:
                    raw_candidates = []

        except Exception as e:
            logger.error(
                "suggest_akshara_error request_id=%s q=%s error=%s",
                request_id,
                q,
                str(e),
            )
            # Don't crash - continue with empty candidates
            raw_candidates = []

        metadata["raw_candidate_count"] = len(raw_candidates)

        # Step 4: Unicode normalization
        normalized_candidates = []
        for cand in raw_candidates:
            if not cand:
                continue
            normalized = normalize_unicode(cand)
            if normalized:
                normalized_candidates.append(normalized)

        # Step 5: Tamil orthography validation
        orthography_valid = []
        for cand in normalized_candidates:
            if validate_tamil_orthography(cand):
                orthography_valid.append(cand)

        metadata["after_rules_count"] = len(orthography_valid)

        # Step 6: Morphological garbage elimination
        morphologically_valid = []
        for cand in orthography_valid:
            if eliminate_morphological_garbage(cand, input_length):
                morphologically_valid.append(cand)

        # Step 7: Lexicon + frequency gating
        lexicon_gated = []
        for cand in morphologically_valid:
            # If input length >= 3, candidate MUST exist in lexicon OR be canonical
            if input_length >= 3:
                if has_freq(cand):
                    lexicon_gated.append(cand)
                # Also allow very short valid syllables (2 chars or less) even if not in lexicon
                elif len(cand) <= 2:
                    lexicon_gated.append(cand)
            else:
                # For short inputs (<=2), allow syllables even if not in lexicon (but still must be valid)
                lexicon_gated.append(cand)

        metadata["after_lexicon_count"] = len(lexicon_gated)

        # If no valid candidates after all filters, return empty
        if not lexicon_gated:
            logger.warning(
                "suggest_no_valid_candidates request_id=%s q=%s", request_id, q
            )
            latency_ms = (time.perf_counter() - start_time) * 1000
            metadata["latency_ms"] = latency_ms
            return [], metadata

        # Step 8: Context-aware ranking
        scored_candidates = []
        for cand in lexicon_gated:
            score = self._rank_candidate(cand, normalized_input, prev)
            scored_candidates.append({"word": cand, "score": score})

        # Sort by score (descending)
        scored_candidates.sort(key=lambda x: x["score"], reverse=True)

        # Step 9: Deterministic cutoff
        final = scored_candidates[:limit]

        metadata["final_count"] = len(final)
        latency_ms = (time.perf_counter() - start_time) * 1000
        metadata["latency_ms"] = latency_ms

        # Step 11: Observability (structured logging)
        logger.info(
            "suggest_complete request_id=%s q=%s raw=%d rules=%d lexicon=%d final=%d latency_ms=%.2f",
            request_id,
            q,
            metadata["raw_candidate_count"],
            metadata["after_rules_count"],
            metadata["after_lexicon_count"],
            metadata["final_count"],
            latency_ms,
        )

        # Step 10: Caching (store result)
        if final:
            self.cache.set(cache_key, final)

        return final, metadata

    def _rank_candidate(
        self, candidate: str, input_text: str, prev: Optional[str] = None
    ) -> float:
        """
        Rank candidate using multiple factors.
        Returns score between 0.0 and 1.0.
        """
        score = 0.0

        # Frequency score (log-scaled)
        freq = freq_score(candidate)
        score += 0.45 * freq

        # Length penalty (prefer shorter, reasonable words)
        length = len(candidate)
        if length <= 6:
            length_score = 1.0
        elif length <= 10:
            length_score = 0.7
        else:
            length_score = 0.5
        score += 0.15 * length_score

        # Short token boost (for inputs <= 2 chars, prefer shorter outputs)
        if len(input_text) <= 2:
            if length == 2:
                score += 0.20  # Perfect match for 2-char input
            elif length <= 2:
                score += 0.15  # Short outputs preferred

        # Bigram boost (if previous word provided) - optional enhancement
        if prev:
            # Simple bigram scoring (can be enhanced with bigram frequency dict)
            score += 0.05

        # Normalize to 0.0-1.0 range
        return min(1.0, max(0.0, score))


# Global service instance
_suggest_service = None


def get_suggest_service() -> SuggestService:
    """Get singleton suggest service instance."""
    global _suggest_service
    if _suggest_service is None:
        _suggest_service = SuggestService()
    return _suggest_service

