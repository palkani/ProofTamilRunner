import logging
import re
import time
from typing import List, Tuple, Set

from app.adapters.aksharamukha import AksharaAdapter
from app.clients.transliterator_client import build_client
from app.core.cache import LRUCache, make_cache_key
from app.core.config import settings

tamil_regex = re.compile(r"^[\u0B80-\u0BFF\s]+$")


def _latin_variants(text: str, max_variants: int = 32) -> List[str]:
    text = (text or "").strip().lower()
    if not text:
        return []
    variants: Set[str] = set()
    variants.add(text)

    # Vowel lengthening
    vowel_map = {"a": "aa", "i": "ii", "u": "uu", "e": "ee", "o": "oo"}
    for i, ch in enumerate(text):
        if ch in vowel_map:
            variants.add(text[: i + 1] + vowel_map[ch] + text[i + 1 :])

    # Consonant tweaks
    variants.add(text.replace("t", "tt"))
    variants.add(text.replace("th", "t"))
    variants.add(text.replace("d", "t"))

    # Common Tamil-ish endings
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


def _score(tier: str) -> float:
    if tier == "base":
        return 1.0
    if tier == "variant":
        return 0.9
    if tier == "suffix":
        return 0.75
    return 0.6


class TransliterationService:
    """
    Provides transliteration with optional external runner and in-memory caching.
    """

    def __init__(self):
        self.adapter = AksharaAdapter()
        self.cache = LRUCache(max_size=settings.CACHE_MAX_SIZE, default_ttl=settings.CACHE_TTL_SECONDS)
        self.client = build_client()
        self.runner_enabled = settings.TRANSLITERATOR_ENABLED

    async def transliterate(
        self, text: str, mode: str, limit: int, request_id: str = "n/a"
    ) -> Tuple[List[dict], bool, str]:
        """
        Transliterate text with cache and external runner if enabled.
        Returns (suggestions, used_runner, cache_status[hit|miss|none]).
        """
        logging.info("transliteration_pipeline_start request_id=%s", request_id)

        text = (text or "").strip()
        if not text or len(text) > settings.MAX_TEXT_LEN:
            logging.warning("[IME] request_id=%s invalid_input len=%d", request_id, len(text))
            return [], False, "none"
        limit = max(1, min(limit or 8, 12))

        key = make_cache_key(text, mode, str(limit))
        logging.info("transliteration_cache_lookup request_id=%s", request_id)
        cached = self.cache.get(key)
        if cached:
            logging.info("transliteration_cache_hit request_id=%s", request_id)
            return cached, True, "hit"
        logging.info("transliteration_cache_miss request_id=%s", request_id)

        suggestions: List[dict] = []
        used_runner = False
        cache_status = "miss"

        # If external runner is enabled, call it first
        if self.runner_enabled:
            if not self.client:
                logging.info(
                    "skipping_transliterator_runner request_id=%s reason=client_not_initialized",
                    request_id,
                )
            else:
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
        try:
            ime_suggestions = await self.generate_ime_suggestions(text, limit)
            suggestions = ime_suggestions if not suggestions else suggestions + ime_suggestions
        except Exception as e:
            logging.exception("[AKSHARA] request_id=%s error=%s", request_id, e)
            return [], False, "none"

        # Dedup and cap
        dedup = {}
        for item in suggestions:
            k = item.get("word")
            if not k:
                continue
            if k not in dedup or item.get("score", 0) > dedup[k].get("score", 0):
                dedup[k] = item
        final = sorted(dedup.values(), key=lambda x: x.get("score", 0), reverse=True)
        final = final[: max(5, min(limit or 8, 10))]

        if final:
            self.cache.set(key, final)
        return final, used_runner, cache_status

    async def generate_ime_suggestions(self, text: str, limit: int) -> List[dict]:
        base_variants = _latin_variants(text, max_variants=32)
        tamil_set: Set[str] = set()
        scored: List[dict] = []

        # Base transliteration of the original token
        base_outs = await self.adapter.transliterate(text, "spoken")
        for out in base_outs:
            if out and tamil_regex.match(out) and out not in tamil_set:
                tamil_set.add(out)
                scored.append({"word": out, "score": _score("base")})

        # Variant transliterations
        for variant in base_variants:
            outs = await self.adapter.transliterate(variant, "spoken")
            for out in outs:
                if out and tamil_regex.match(out) and out not in tamil_set:
                    tamil_set.add(out)
                    scored.append({"word": out, "score": _score("variant")})

        # Tamil suffix expansion
        expanded: List[str] = []
        for w in list(tamil_set):
            expanded.extend(_tamil_suffix_expansion(w))
        for exp in expanded:
            if exp and tamil_regex.match(exp) and exp not in tamil_set:
                tamil_set.add(exp)
                scored.append({"word": exp, "score": _score("suffix")})

        # Dedup, sort, trim
        dedup = {}
        for item in scored:
            key = item["word"]
            if key not in dedup or item["score"] > dedup[key]["score"]:
                dedup[key] = item
        suggestions = sorted(dedup.values(), key=lambda x: x["score"], reverse=True)
        suggestions = suggestions[: max(5, min(limit or 8, 10))]

        logging.info(
            "[IME] base=%s variants=%d suggestions=%d",
            text,
            len(base_variants),
            len(suggestions),
        )

        return suggestions
