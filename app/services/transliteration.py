import logging
import re
import time
from typing import List

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

    async def transliterate(self, text: str, mode: str, limit: int, request_id: str = "n/a") -> List[dict]:
        """
        Transliterate text with cache; uses external runner if configured.
        """
        text = (text or "").strip()
        if not text or len(text) > settings.MAX_TEXT_LEN:
            logging.warning("[IME] request_id=%s invalid_input len=%d", request_id, len(text))
            return []
        limit = max(1, min(limit or 8, 12))

        key = make_cache_key(text, mode, str(limit))
        cached = self.cache.get(key)
        if cached:
            logging.info(
                "[IME] event=cache_hit service=prooftamil-backend dependency=transliterator request_id=%s cache_status=hit",
                request_id,
            )
            return cached

        start = time.perf_counter()
        try:
            if self.client:
                data = await self.client.transliterate(text)
                outputs = [s.get("word") or s.get("ta") for s in data.get("suggestions", []) if s]
            else:
                outputs = await self.adapter.transliterate(text, mode)
            latency_ms = (time.perf_counter() - start) * 1000
            logging.info(
                "[AKSHARA] request_id=%s event=ok service=prooftamil-backend dependency=transliterator outputs=%d latency_ms=%.2f cache_status=miss",
                request_id,
                len(outputs),
                latency_ms,
            )
        except Exception as e:
            logging.exception("[AKSHARA] request_id=%s error=%s", request_id, e)
            return []

        suggestions = []
        for out in outputs:
            if not out or not tamil_regex.match(out):
                continue
            suggestions.append({"word": out, "ta": out, "score": 1.0})
            if len(suggestions) >= limit:
                break

        self.cache.set(key, suggestions)
        logging.info(
            "[IME] event=cache_store service=prooftamil-backend dependency=transliterator request_id=%s cache_status=miss",
            request_id,
        )
        return suggestions
