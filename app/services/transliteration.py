import logging
import re
import time
import math
from typing import List, Tuple, Set

from app.adapters.aksharamukha import AksharaAdapter
from app.clients.transliterator_client import build_client
from app.core.cache import LRUCache, make_cache_key
from app.core.config import settings
from app.core.freq_dict import freq_score, has_freq

DEBUG = False

tamil_regex = re.compile(r"^[\u0B80-\u0BFF\s]+$")

# PART 3: Tamil dependent vowels (vowel signs that attach to consonants)
# These cannot be stacked sequentially
DEPENDENT_VOWELS: Set[str] = {
    'ா', 'ி', 'ீ', 'ு', 'ூ', 'ெ', 'ே', 'ை', 'ொ', 'ோ', 'ௌ'
}


def has_invalid_vowel_sequence(word: str) -> bool:
    """
    Check if a Tamil word has invalid vowel sequences.
    Two dependent vowels in a row is linguistically invalid.
    """
    if not word or len(word) < 2:
        return False

    for i in range(1, len(word)):
        prev = word[i - 1]
        curr = word[i]

        # Two dependent vowels in a row is invalid
        if prev in DEPENDENT_VOWELS and curr in DEPENDENT_VOWELS:
            return True

    return False


def filter_tamil_suggestions(suggestions: List[dict], token: str) -> List[dict]:
    """
    PART 3: Filter Tamil suggestions to remove invalid forms.
    - Rejects Latin/digits
    - Rejects invalid vowel stacking
    - Rejects overly long expansions for short inputs
    """
    if not suggestions:
        return []

    # For short tokens (1-2 chars), limit to 3 chars max
    # For longer tokens, allow up to 6 chars
    max_len = 3 if len(token) <= 2 else 6

    filtered = []
    for s in suggestions:
        w = (s.get("word") or s.get("ta") or "").strip()
        if not w:
            continue

        # Reject Latin / digits (must be pure Tamil)
        if any(c.isascii() and c.isalnum() for c in w):
            continue

        # Reject invalid vowel stacking
        if has_invalid_vowel_sequence(w):
            continue

        # Reject too-long expansions for short input
        if len(w) > max_len:
            continue

        filtered.append(s)

    return filtered


TAMIL_VOWELS = set('அஆஇஈஉஊஎஏஐஒஓஔ')
TAMIL_VOWEL_SIGNS = set('ாிீுூெேைொோௌ')

def ends_with_tamil_vowel(word: str) -> bool:
    if not word:
        return False
    ch = word[-1]
    if ch in TAMIL_VOWELS or ch in TAMIL_VOWEL_SIGNS:
        return True
    return False


def _latin_variants(text: str, max_variants: int = 64) -> List[str]:
    text = (text or "").strip().lower()
    if not text:
        return []
    variants: Set[str] = set()
    variants.add(text)

    vowel_map = {"a": "aa", "i": "ii", "u": "uu", "e": "ee", "o": "oo"}
    for i, ch in enumerate(text):
        if ch in vowel_map:
            variants.add(text[: i + 1] + vowel_map[ch] + text[i + 1 :])

    variants.add(text.replace("t", "tt"))
    variants.add(text.replace("th", "t"))
    variants.add(text.replace("d", "t"))

    endings = ["i", "ai", "a", "u", "oo", "ta", "tai", "tta", "ttai", "di", "ti"]
    for end in endings:
        variants.add(text + end)

    return list(list(variants)[:max_variants])


def _tamil_suffix_expansion(word: str) -> List[str]:
    if not word:
        return []
    # Do not append vowel signs if the word already ends with a vowel or vowel sign
    if ends_with_tamil_vowel(word):
        return [word]
    forms = {word}
    suffixes = ["ி", "ை", "ு", "ா"]
    for suf in suffixes:
        forms.add(word + suf)
    if word.endswith("ட்"):
        forms.add(word[:-1] + "ட்ட")
        forms.add(word[:-1] + "ட்டை")
    return list(forms)



def _normalize(s: str) -> str:
    return (s or "").lower()


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = curr
    return prev[-1]


def _phonetic_score(src: str, tgt: str) -> float:
    a = _normalize(src)
    b = _normalize(tgt)
    if not a or not b:
        return 0.5
    dist = _levenshtein(a, b)
    max_len = max(len(a), len(b)) or 1
    sim = 1 - dist / max_len
    return max(0.0, min(1.0, sim))


def _length_score(word: str) -> float:
    l = len(word)
    if l <= 1:
        return 0.3
    if 2 <= l <= 6:
        return 1.0
    if l <= 10:
        return 0.7
    return 0.5


def _form_score(word: str) -> float:
    if not word:
        return 0.5
    if word.endswith("்") and not has_freq(word):
        return 0.6
    return 1.0


def _score_candidate(word: str, src_variant: str, tier: str) -> float:
    freq = freq_score(word)
    phon = _phonetic_score(src_variant, word)
    form = _form_score(word)
    length = _length_score(word)
    final = 0.45 * freq + 0.30 * phon + 0.15 * form + 0.10 * length
    return round(min(1.0, max(0.0, final)), 2)


class TransliterationService:
    """
    Provides transliteration with optional external runner and in-memory caching.
    """

    def __init__(self):
        self.adapter = AksharaAdapter()
        self.cache = LRUCache(max_size=settings.CACHE_MAX_SIZE, default_ttl=settings.CACHE_TTL_SECONDS)
        self.response_cache = LRUCache(max_size=20000, default_ttl=1800)
        self.variant_cache = LRUCache(max_size=5000, default_ttl=900)
        self.client = build_client()
        self.runner_enabled = settings.TRANSLITERATOR_ENABLED

    async def transliterate(
        self, text: str, mode: str, limit: int, request_id: str = "n/a"
    ) -> Tuple[List[dict], bool, str]:
        logging.info("transliteration_pipeline_start request_id=%s", request_id)

        text = (text or "").strip()
        if not text or len(text) > settings.MAX_TEXT_LEN:
            logging.warning("[IME] request_id=%s invalid_input len=%d", request_id, len(text))
            return [], False, "none"
        limit = max(1, min(limit or 8, 12))

        key = make_cache_key(text, mode, str(limit))
        cached = self.response_cache.get(key)
        if cached:
            if DEBUG:
                logging.info("[IME] cache hit q=%s", text)
            return cached, True, "hit"

        suggestions: List[dict] = []
        used_runner = False
        cache_status = "miss"

        # External runner (if enabled)
        if self.runner_enabled:
            if not self.client:
                logging.info(
                    "skipping_transliterator_runner request_id=%s reason=client_not_initialized",
                    request_id,
                )
            else:
                if DEBUG:
                    logging.info("calling_transliterator_runner request_id=%s", request_id)
                start = time.perf_counter()
                try:
                    data = await self.client.transliterate(text)
                    outputs = [s.get("word") or s.get("ta") for s in data.get("suggestions", []) if s]
                    latency_ms = (time.perf_counter() - start) * 1000
                    logging.info(
                        "transliterator_runner_success request_id=%s latency_ms=%.2f outputs=%d",
                        request_id,
                        latency_ms,
                        len(outputs),
                    )
                    used_runner = True
                    # Build raw suggestions list first
                    raw_suggestions = []
                    for out in outputs:
                        if not out or not tamil_regex.match(out):
                            continue
                        raw_suggestions.append({"word": out, "score": 1.0})
                    
                    # PART 3: Filter suggestions to remove invalid Tamil forms
                    filtered_suggestions = filter_tamil_suggestions(raw_suggestions, text)
                    
                    # Fallback: if everything filtered out, keep only first raw item as safe fallback
                    if filtered_suggestions:
                        suggestions = filtered_suggestions[:limit]
                    else:
                        suggestions = raw_suggestions[:1] if raw_suggestions else []
                except Exception as e:
                    logging.error(
                        "transliterator_runner_failure request_id=%s error=%s", request_id, str(e)
                    )
        else:
            logging.info(
                "skipping_transliterator_runner request_id=%s reason=disabled enabled=%s base_url_present=%s",
                request_id,
                self.runner_enabled,
                bool(settings.TRANSLITERATOR_BASE_URL),
            )

        # IME-style generation with Aksharamukha (fallback or supplement)
        if not used_runner or not suggestions:
            ime_suggestions = await self.generate_ime_suggestions(text, limit)
            # PART 3: Filter IME suggestions to remove invalid Tamil forms
            filtered_ime = filter_tamil_suggestions(ime_suggestions, text)
            if filtered_ime:
                suggestions = suggestions + filtered_ime if suggestions else filtered_ime
            elif not suggestions:
                suggestions = ime_suggestions[:1] if ime_suggestions else []

        # Dedup, normalize, and cap
        cleaned = {}
        for item in suggestions:
            word = item.get("word") if isinstance(item, dict) else None
            if not word:
                continue
            try:
                score = round(float(item.get("score", 0)), 2)
            except Exception:
                continue
            if word not in cleaned or score > cleaned[word]["score"]:
                cleaned[word] = {"word": word, "score": score}

        # Apply final filtering to Tamil words only and normalize
        filtered = []
        for s in cleaned.values():
            w = s.get("word")
            if not w or not tamil_regex.match(w):
                continue
            # Additional filter: reject invalid vowel sequences
            if has_invalid_vowel_sequence(w):
                continue
            filtered.append({"word": w, "score": s.get("score", 0)})

        final = sorted(filtered, key=lambda x: x["score"], reverse=True)
        final = final[: max(8, min(limit or 8, 10))]

        assert all("word" in s and "score" in s for s in final)

        if DEBUG:
            logging.info("[IME] final suggestions=%s", final)

        if final:
            self.response_cache.set(key, final)
        return final, used_runner, cache_status

    async def generate_ime_suggestions(self, text: str, limit: int) -> List[dict]:
        base_variants = _latin_variants(text, max_variants=64)
        tamil_set: Set[str] = set()
        scored: List[dict] = []

        async def translit_cached(token: str) -> List[str]:
            ck = make_cache_key("variant", token)
            cached = self.variant_cache.get(ck)
            if cached:
                return cached
            outs = await self.adapter.transliterate(token, "spoken")
            self.variant_cache.set(ck, outs)
            return outs

        # Base transliteration of the original token
        for out in await translit_cached(text):
            if out and tamil_regex.match(out) and out not in tamil_set:
                tamil_set.add(out)
                scored.append({"word": out, "score": _score_candidate(out, text, "base")})

        # Variant transliterations
        for variant in base_variants:
            outs = await translit_cached(variant)
            for out in outs:
                if out and tamil_regex.match(out) and out not in tamil_set:
                    tamil_set.add(out)
                    scored.append({"word": out, "score": _score_candidate(out, variant, "variant")})
            if len(tamil_set) > 80:
                break

        # Tamil suffix expansion
        expanded: List[str] = []
        for w in list(tamil_set):
            expanded.extend(_tamil_suffix_expansion(w))
        for exp in expanded:
            if exp and tamil_regex.match(exp) and exp not in tamil_set:
                tamil_set.add(exp)
                scored.append({"word": exp, "score": _score_candidate(exp, text, "suffix")})

        dedup = {}
        for item in scored:
            key = item["word"]
            if key not in dedup or item["score"] > dedup[key]["score"]:
                dedup[key] = item
        suggestions = sorted(dedup.values(), key=lambda x: x["score"], reverse=True)
        suggestions = suggestions[: max(8, min(limit or 8, 10))]

        if DEBUG:
            logging.info(
                "[IME] base=%s variants=%d suggestions=%d",
                text,
                len(base_variants),
                len(suggestions),
            )

        return suggestions
