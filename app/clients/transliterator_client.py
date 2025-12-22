"""
HTTP client for the external Transliterator runner service.
"""
import logging
import os
import time
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


class TransliteratorClient:
    def __init__(self, base_url: str, timeout_seconds: int = 5):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds

    async def transliterate(self, text: str) -> Dict[str, Any]:
        """
        Call the external runner to transliterate text.
        Raises RuntimeError on failures.
        """
        url = f"{self.base_url}/api/v1/transliterate"
        payload = {"text": text}
        timeout = httpx.Timeout(self.timeout)
        start = time.perf_counter()
        logger.info(
            "[TRANSCLIENT] event=start service=prooftamil-backend dependency=transliterator url=%s cache_status=miss",
            url,
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                resp = await client.post(url, json=payload)
                latency_ms = (time.perf_counter() - start) * 1000
                if resp.status_code != 200:
                    logger.error(
                        "[TRANSCLIENT] event=error status=%s service=prooftamil-backend dependency=transliterator latency_ms=%.2f",
                        resp.status_code,
                        latency_ms,
                    )
                    raise RuntimeError(f"Transliterator error status {resp.status_code}")
                data = resp.json()
                logger.info(
                    "[TRANSCLIENT] event=ok service=prooftamil-backend dependency=transliterator latency_ms=%.2f",
                    latency_ms,
                )
                return data
            except Exception as e:
                logger.exception("[TRANSCLIENT] event=error service=prooftamil-backend dependency=transliterator err=%s", e)
                raise


def build_client() -> Optional[TransliteratorClient]:
    base = os.environ.get("TRANSLITERATOR_BASE_URL")
    if not base:
        return None
    timeout = int(os.environ.get("TRANSLITERATOR_TIMEOUT_SECONDS", "5"))
    return TransliteratorClient(base_url=base, timeout_seconds=timeout)
