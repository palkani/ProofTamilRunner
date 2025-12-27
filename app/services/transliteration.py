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
                    for out in outputs:
                        if not out or not tamil_regex.match(out):
                            continue
                        suggestions.append({"word": out, "score": 1.0})
                        if len(suggestions) >= limit:
                            break
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
        ime_suggestions = await self.generate_ime_suggestions(text, limit)
        suggestions = suggestions + ime_suggestions if suggestions else ime_suggestions

        # Dedup and cap
        dedup = {}
        for item in suggestions:
            k = item.get("word")
            if not k:
                continue
            if k not in dedup or item.get("score", 0) > dedup[k].get("score", 0):
                dedup[k] = item
        final = sorted(dedup.values(), key=lambda x: x.get("score", 0), reverse=True)
        final = final[: max(8, min(limit or 8, 10))]

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
