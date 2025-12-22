import logging
import re
import time
from typing import List, Tuple

from app.adapters.aksharamukha import AksharaAdapter
from app.clients.transliterator_client import build_client
from app.core.cache import LRUCache, make_cache_key
from app.core.config import settings

tamil_regex = re.compile(r"^[\u0B80-\u0BFF\s]+$")


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
                        suggestions.append({"word": out, "ta": out, "score": 1.0})
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

        if used_runner:
            if suggestions:
                self.cache.set(key, suggestions)
            return suggestions, True, cache_status

        # Runner not used or failed; fallback to adapter (Gemini/legacy path)
        try:
            outputs = await self.adapter.transliterate(text, mode)
            logging.info(
                "[AKSHARA] request_id=%s event=fallback_adapter outputs=%d", request_id, len(outputs)
            )
            for out in outputs:
                if not out or not tamil_regex.match(out):
                    continue
                suggestions.append({"word": out, "ta": out, "score": 1.0})
                if len(suggestions) >= limit:
                    break
        except Exception as e:
            logging.exception("[AKSHARA] request_id=%s error=%s", request_id, e)
            return [], False, "none"

        return suggestions, False, "none"
