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
    expand_common_variants,
)
from app.services.canonical_map import get_canonical
from app.services.corpus_db import get_corpus

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
            # Very common kinship term: ensure we show real word forms (avoid partial "அம்ம").
            "amma": ["அம்மா", "அம்மாவை", "அம்மாவின்", "அம்மாவுக்கு", "அம்மாவுடன்", "அம்மாவிடம்", "அம்மாவுடைய", "அம்மாக்கள்"],
            # Google-IME-like variants for common nouns
            # nanban -> friend (Tamil): provide common inflections and related forms
            "nanban": ["நண்பன்", "நண்பா", "நண்பனை", "நண்பனே", "நண்பர்", "நண்பர்கள்", "நண்பனுக்கு", "நண்பனுடன்", "நண்பனிடம்", "நண்பனுடைய"],
            # mozhi/ மொழி: Aksharamukha can sometimes produce "மொலி" for "moli".
            # Force meaningful variants (Google-IME-like) for language/word usage.
            "moli": ["மொழி", "மொழியை", "மொழியில்", "மொழியால்", "மொழிகள்", "மொழியுடன்"],
            "mozhi": ["மொழி", "மொழியை", "மொழியில்", "மொழியால்", "மொழிகள்", "மொழியுடன்"],
            # Colloquial/spoken past tense patterns (-ichiya, -chiya question forms)
            # padichiya -> படிச்சியா (did you study/read? - spoken Tamil)
            "padichiya": ["படிச்சியா", "படிச்சிய", "படித்தியா", "படிச்சா", "படிச்சீயா", "படிச்சேன்", "படிச்சான்", "படிச்சாங்க"],
            "padicha": ["படிச்சா", "படிச்ச", "படித்தா", "படிச்சான்", "படிச்சாங்க"],
            "padichen": ["படிச்சேன்", "படிச்சேனா", "படித்தேன்"],
            # Common food words - Aksharamukha returns wrong forms
            "soru": ["சோறு", "சோற்றை", "சோற்றில்", "சாதம்", "சாப்பாடு"],
            "choru": ["சோறு", "சோற்றை", "சோற்றில்"],
            "saatham": ["சாதம்", "சாதத்தை", "சாதத்தில்"],
            "sapadu": ["சாப்பாடு", "சாப்பாட்டை", "சாப்பாட்டில்", "சாப்பிட"],
            "saappadu": ["சாப்பாடு", "சாப்பாட்டை", "சாப்பாட்டில்", "சாப்பிட"],
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
            # For canonical outputs, optionally expand to multiple useful variants (Google-IME-like),
            # but keep very short tokens strict to avoid junk (e.g., mu -> "முயில்" style accidents).
            variants = [canonical_output]
            if input_length >= 3 and limit >= 6:
                # Only keep expansions that look real (dictionary-backed) to avoid generating
                # invalid words for some vowel-ending lemmas.
                expanded = expand_common_variants(canonical_output, max_variants=limit) or [canonical_output]
                variants = [canonical_output] + [v for v in expanded if v != canonical_output and has_freq(v)]

            result = []
            for idx, w in enumerate(variants[:limit]):
                # Keep only structurally valid expansions
                if is_structurally_invalid_tamil(w):
                    continue
                score = max(0.55, 1.0 - idx * 0.1)
                result.append(
                    {
                        "word": w,
                        "score": 1.0 if idx == 0 else round(score, 2),
                        "source": "canonical",
                    }
                )
            if not result:
                result = [{"word": canonical_output, "score": 1.0, "source": "canonical"}]
            self.cache.set(cache_key, result)
            metadata["final_count"] = len(result)
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

        # Step 2.5: CORPUS DATABASE QUERY (PRIMARY SOURCE)
        # Query the corpus DB BEFORE calling Aksharamukha.
        # This ensures we return high-quality, user-validated Tamil words first.
        corpus = get_corpus()
        corpus_suggestions = []
        try:
            corpus_suggestions = corpus.suggest_words(normalized_input, limit=limit)
            if corpus_suggestions:
                logger.info(
                    "suggest_corpus_hit request_id=%s q=%s count=%d sample=%s",
                    request_id,
                    q,
                    len(corpus_suggestions),
                    corpus_suggestions[:3]
                )
                # Corpus returned results - format and return immediately
                result = []
                for idx, (tamil_word, score) in enumerate(corpus_suggestions):
                    result.append({
                        "word": tamil_word,
                        "score": score,
                        "source": "corpus_db"
                    })
                self.cache.set(cache_key, result)
                latency_ms = (time.perf_counter() - start_time) * 1000
                metadata["source"] = "corpus_db"
                metadata["final_count"] = len(result)
                metadata["latency_ms"] = round(latency_ms, 2)
                logger.info(
                    "suggest_corpus_return request_id=%s q=%s latency_ms=%.2f count=%d",
                    request_id,
                    q,
                    latency_ms,
                    len(result)
                )
                return result, metadata
        except Exception as e:
            logger.warning(
                "suggest_corpus_error request_id=%s q=%s error=%s",
                request_id,
                q,
                str(e)
            )
            # Continue to Aksharamukha fallback

        # Step 3: Candidate generation via Aksharamukha (FALLBACK)
        # Only reach here if corpus didn't have suggestions
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

        # DEBUG: Log the first few raw candidates for problematic queries
        if input_length >= 7 or normalized_input in {"padichiya", "nanban", "sapadu", "soru", "amma"}:
            logger.info(
                "suggest_raw_candidates request_id=%s q=%s count=%d sample=%s",
                request_id,
                q,
                len(raw_candidates),
                raw_candidates[:5] if len(raw_candidates) > 0 else []
            )

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
            # For very short roman inputs, keep strict Tamil orthography rules to prevent junk
            # (e.g., mu -> முஉ).
            # For longer inputs (often English loanwords), Aksharamukha can emit Tamil-script
            # forms that violate strict vowel-sequence rules; showing something is better than
            # showing nothing, so we relax the structural gate.
            if is_structurally_invalid_tamil(cand):
                if input_length <= 3:
                    continue
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
        # Step 5.5 (new): Confusable repair.
        # Aksharamukha sometimes returns plausible-but-wrong variants (short/long vowels, ர/ற, etc.).
        # We generate a small set of "nearby" Tamil-script variants and keep only those that appear
        # in our frequency dictionary, then let the scorer/ranker do the rest.
        def expand_confusable_variants(word: str, max_out: int = 32) -> List[str]:
            w0 = normalize_unicode(word)
            if not w0:
                return []
            seen = set()
            out = []

            def add(w: str):
                w = normalize_unicode(w)
                if not w or w in seen:
                    return
                seen.add(w)
                out.append(w)

            add(w0)

            # Focus edits on the tail to reduce blow-up.
            chars = list(w0)
            tail_start = max(0, len(chars) - 6)
            positions = range(tail_start, len(chars))

            # Prefer generating long-vowel alternatives and common confusable consonants.
            swap1 = {
                "ொ": "ோ",
                "ெ": "ே",
                "ி": "ீ",
                "ு": "ூ",
                "ர": "ற",
                "ற": "ர",
            }

            # Single edits
            for i in positions:
                ch = chars[i]
                rep = swap1.get(ch)
                if not rep:
                    continue
                c2 = chars.copy()
                c2[i] = rep
                add("".join(c2))
                if len(out) >= max_out:
                    return out

            # Two edits (bounded): apply one more edit on already-generated variants.
            for base in out[:]:
                if len(out) >= max_out:
                    break
                bchars = list(base)
                for i in range(max(0, len(bchars) - 6), len(bchars)):
                    ch = bchars[i]
                    rep = swap1.get(ch)
                    if not rep:
                        continue
                    c2 = bchars.copy()
                    c2[i] = rep
                    add("".join(c2))
                    if len(out) >= max_out:
                        break

            return out

        scored_candidates = []
        # Seed repair from the first few candidates only.
        repaired = list(structurally_valid)
        existing = set(repaired)
        for seed in structurally_valid[:6]:
            for v in expand_confusable_variants(seed):
                if v in existing:
                    continue
                # Only keep variants that are dictionary-backed; this prunes nonsense hard.
                if not has_freq(v):
                    continue
                if is_structurally_invalid_tamil(v):
                    continue
                repaired.append(v)
                existing.add(v)
                if len(repaired) >= (len(structurally_valid) + 40):
                    break
            if len(repaired) >= (len(structurally_valid) + 40):
                break

        for cand in repaired:
            score = self._rank_candidate(cand, normalized_input, prev, input_length)
            scored_candidates.append({"word": cand, "score": score})

        # Sort by score (descending)
        scored_candidates.sort(key=lambda x: x["score"], reverse=True)

        # Step 8.5 (new): Auto-expand variants to provide "many suggestions" like Google IME.
        # We only do this for word-level modes and for non-trivial inputs (avoid junk on very short tokens).
        if input_length >= 3 and limit >= 6 and scored_candidates:
            expanded = []
            # Expand from top-1 (and optionally top-2) base candidates
            bases = [scored_candidates[0]]
            if len(scored_candidates) > 1:
                bases.append(scored_candidates[1])

            existing = {x["word"] for x in scored_candidates}
            for base in bases:
                base_word = base.get("word", "")
                base_score = float(base.get("score", 0.6) or 0.6)
                variants = expand_common_variants(base_word, max_variants=max(6, limit))
                for idx, v in enumerate(variants):
                    if v in existing:
                        continue
                    # Keep only structurally valid expansions
                    if is_structurally_invalid_tamil(v):
                        continue
                    # Keep expansions dictionary-backed; avoids incorrect "…ுய…" garbage forms.
                    if not has_freq(v):
                        continue
                    # Slightly decay score for expansions, keep stable ordering
                    s = max(0.3, min(0.98, base_score * 0.92 - (idx * 0.03)))
                    expanded.append({"word": v, "score": s})
                    existing.add(v)
                    if len(expanded) >= (limit * 2):
                        break
                if len(expanded) >= (limit * 2):
                    break

            if expanded:
                scored_candidates.extend(expanded)
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

