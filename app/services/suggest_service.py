"""
Production-grade suggest API service implementing 12-step pipeline.
"""

import logging
import math
import time
import unicodedata
from functools import lru_cache
from typing import List, Dict, Optional, Tuple

from app.adapters.aksharamukha import AksharaAdapter
from app.core.cache import LRUCache, make_cache_key
from app.core.config import settings
from app.core.freq_dict import freq_score, has_freq
from app.services.tamil_linguistics import (
    normalize_roman_input,
    normalize_unicode,
    is_structurally_invalid_tamil,
    morphology_score,
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
            "after_structural_filter": 0,
            "final_count": 0,
        }

        # PART B: Character-level IME (mode == "char")
        # Check mode parameter - if "char", use character-level transliteration
        normalized_q = q.strip()
        if mode == "char":
            return self._suggest_char_level(normalized_q, limit, request_id)

        # Step 1: Roman input normalization
        normalized_input = normalize_roman_input(q)
        input_length = len(normalized_input)

        # Step 1.5: Ranked overrides for a few high-signal tokens (competitor-style).
        # These are returned exactly, to guarantee correctness and UX consistency.
        ranked_overrides = {
            "enpathu": ["என்பது", "எண்பது", "எண்பத்து", "என்பத்து", "எண்பத"],
        }
        forced = ranked_overrides.get(normalized_input)
        if forced:
            result = []
            for idx, w in enumerate(forced[:limit]):
                score = max(0.55, 1.0 - idx * 0.1)
                result.append({"word": w, "score": 1.0 if idx == 0 else round(score, 2), "source": "override"})
            self.cache.set(cache_key, result)
            latency_ms = (time.perf_counter() - start_time) * 1000
            return result, {"cache": "miss", "source": "override", "final_count": len(result), "latency_ms": round(latency_ms, 2)}

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

        # PART B: Minimal hard filter - only structurally invalid Tamil
        structurally_valid = []
        for cand in normalized_candidates:
            if not is_structurally_invalid_tamil(cand):
                structurally_valid.append(cand)

        metadata["after_structural_filter"] = len(structurally_valid)

        # If no structurally valid candidates, return empty
        if not structurally_valid:
            logger.warning(
                "suggest_no_structurally_valid request_id=%s q=%s", request_id, q
            )
            latency_ms = (time.perf_counter() - start_time) * 1000
            metadata["latency_ms"] = latency_ms
            return [], metadata

        # PART C: Soft scoring (no hard filtering beyond structural validity)
        scored_candidates = []
        for cand in structurally_valid:
            score = self._rank_candidate(cand, normalized_input, prev, input_length)
            scored_candidates.append({"word": cand, "score": score})

        # Sort by score (descending)
        scored_candidates.sort(key=lambda x: x["score"], reverse=True)

        # Step 9: Return top N (respect limit, but ensure we return meaningful results)
        final = scored_candidates[:limit]

        metadata["final_count"] = len(final)
        latency_ms = (time.perf_counter() - start_time) * 1000
        metadata["latency_ms"] = latency_ms

        # Step 11: Observability (structured logging)
        logger.info(
            "suggest_complete request_id=%s q=%s raw=%d structural=%d final=%d latency_ms=%.2f",
            request_id,
            q,
            metadata["raw_candidate_count"],
            metadata.get("after_structural_filter", 0),
            metadata["final_count"],
            latency_ms,
        )

        # Step 10: Caching (store result)
        if final:
            self.cache.set(cache_key, final)

        return final, metadata

    def _phonetic_score(self, input_text: str, candidate: str) -> float:
        """
        PART C: Phonetic similarity score (0.0-1.0).
        Reverse-transliterate Tamil -> Roman and compare on Roman.
        Comparing Roman input directly against Tamil output (different scripts) is meaningless.
        """
        if not input_text or not candidate:
            return 0.5
        
        a = input_text.lower().strip()
        t = (candidate or "").strip()
        
        if not a or not t:
            return 0.5

        @lru_cache(maxsize=4096)
        def tamil_to_roman(tamil: str) -> str:
            try:
                from aksharamukha.transliterate import process
                out = process("Tamil", "ISO", tamil)
                return (out or "").lower().strip()
            except Exception:
                return ""

        roman = tamil_to_roman(t)
        if not roman:
            return 0.5

        if a == roman:
            return 1.0
        
        # Simple edit distance (Levenshtein)
        def levenshtein(s1: str, s2: str) -> int:
            if len(s1) < len(s2):
                return levenshtein(s2, s1)
            if len(s2) == 0:
                return len(s1)
            
            previous_row = list(range(len(s2) + 1))
            for i, c1 in enumerate(s1):
                current_row = [i + 1]
                for j, c2 in enumerate(s2):
                    insertions = previous_row[j + 1] + 1
                    deletions = current_row[j] + 1
                    substitutions = previous_row[j] + (c1 != c2)
                    current_row.append(min(insertions, deletions, substitutions))
                previous_row = current_row
            return previous_row[-1]
        
        dist = levenshtein(a, roman)
        max_len = max(len(a), len(roman)) or 1
        similarity = 1.0 - (dist / max_len)
        return max(0.0, min(1.0, similarity))
    
    def _length_score(self, candidate: str) -> float:
        """
        PART C: Length score (0.0-1.0).
        Favors 2-6 chars, penalizes very long or very short.
        """
        length = len(candidate)
        if 2 <= length <= 6:
            return 1.0
        elif length == 1:
            return 0.3
        elif length == 7 or length == 8:
            return 0.8
        elif length == 9 or length == 10:
            return 0.6
        else:
            return 0.4

    def _suggest_char_level(
        self, char: str, limit: int, request_id: str
    ) -> Tuple[List[Dict[str, any]], Dict[str, any]]:
        """
        PART B: Character-level IME - generate Tamil letters for single Latin character.
        
        Examples:
        t → ட, த, ட், த்
        n → ந, ண, ன
        k → க, க்
        
        Returns basic Tamil letters/syllables with phonetic scoring only.
        """
        if not char or len(char) != 1:
            return [], {"error": "char mode requires single character"}
        
        char_lower = char.lower().strip()
        if not char_lower or not char_lower.isalpha():
            return [], {"error": "invalid character"}
        
        # Character to Tamil letter mappings (phonetic expansions)
        CHAR_MAP = {
            't': ['த', 'ட', 'த்', 'ட்'],  # ta, Da, th, Dh
            'n': ['ந', 'ண', 'ன'],  # na, Na, nna
            'k': ['க', 'க்'],  # ka, k
            'p': ['ப', 'ப்'],  # pa, p
            'm': ['ம', 'ம்'],  # ma, m
            'r': ['ர', 'ற'],  # ra, Ra
            'l': ['ல', 'ள', 'ழ'],  # la, La, zha
            's': ['ச', 'ஸ', 'ஷ'],  # ca, sa, sha
            'y': ['ய', 'ய்'],  # ya, y
            'v': ['வ', 'வ்'],  # va, v
            'c': ['ச', 'ச்'],  # ca, c
            'h': ['ஹ', 'ஹ்'],  # ha, h
            'd': ['த', 'ட'],  # tha, Da
            'b': ['ப', 'ப்'],  # ba, b
            'g': ['க', 'க்'],  # ga, g
            'j': ['ஜ', 'ஜ்'],  # ja, j
            'z': ['ஸ', 'ஸ்'],  # za, z
            'f': ['ஃ', 'ஃப்'],  # special Tamil character
            'x': ['ஸ்'],  # ks
            'q': ['க்'],  # q -> k
            'w': ['வ'],  # w -> v
        }
        
        # Get Tamil letters for this character
        candidates = CHAR_MAP.get(char_lower, [])
        
        # If no direct mapping, try using aksharamukha library directly for basic transliteration
        if not candidates:
            try:
                from aksharamukha.transliterate import process
                # Try basic transliteration (synchronous call)
                transliterated = process("ISO", "Tamil", char_lower)
                if transliterated and len(transliterated) > 0:
                    # Extract single Tamil characters (filter out non-Tamil)
                    tamil_chars = [c for c in transliterated if '\u0B80' <= c <= '\u0BFF' or c in ['ா', 'ி', 'ீ', 'ு', 'ூ', 'ெ', 'ே', 'ை', 'ொ', 'ோ', 'ௌ', '்']]
                    if tamil_chars:
                        candidates = list(set(tamil_chars[:8]))  # Limit to 8 unique chars
            except Exception as e:
                logger.warning(f"char_mode_aksharamukha_failed char={char_lower} error={str(e)}")
        
        if not candidates:
            return [], {"error": "no candidates found"}
        
        # Score by phonetic closeness (simple - all get similar score for char mode)
        scored = []
        for i, cand in enumerate(candidates[:limit * 2]):  # Get more candidates initially
            # First candidate gets highest score, rest slightly lower
            score = 1.0 - (i * 0.1)
            scored.append({"word": cand, "score": max(0.1, score)})
        
        # Sort by score (descending) and limit
        scored.sort(key=lambda x: x["score"], reverse=True)
        final = scored[:min(limit, 8)]
        
        metadata = {
            "mode": "char",
            "char": char_lower,
            "final_count": len(final),
        }
        
        logger.info(f"char_mode_suggest request_id={request_id} char={char_lower} count={len(final)}")
        return final, metadata

    def _rank_candidate(
        self, candidate: str, input_text: str, prev: Optional[str] = None, input_length: int = 0
    ) -> float:
        """
        PART C: Soft scoring with weighted factors.
        
        FinalScore = 0.40 * phoneticScore + 0.35 * frequencyScore + 0.15 * morphologyScore + 0.10 * lengthScore
        
        Returns score between 0.0 and 1.0.
        """
        # 0.40 * phoneticScore
        phonetic = self._phonetic_score(input_text, candidate)
        weighted_phonetic = 0.40 * phonetic

        # 0.35 * frequencyScore (0.0 if not in dictionary, log-scaled if present)
        freq = freq_score(candidate)
        weighted_freq = 0.35 * freq

        # 0.15 * morphologyScore (soft penalties for odd forms)
        morph = morphology_score(candidate, input_length)
        weighted_morph = 0.15 * morph

        # 0.10 * lengthScore (favors 2-6 chars)
        length = self._length_score(candidate)
        weighted_length = 0.10 * length

        final_score = weighted_phonetic + weighted_freq + weighted_morph + weighted_length
        
        # Normalize to 0.0-1.0 range (should already be in range, but ensure)
        return max(0.0, min(1.0, final_score))


# Global service instance
_suggest_service = None


def get_suggest_service() -> SuggestService:
    """Get singleton suggest service instance."""
    global _suggest_service
    if _suggest_service is None:
        _suggest_service = SuggestService()
    return _suggest_service

