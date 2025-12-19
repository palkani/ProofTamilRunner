import logging
import re
from typing import List
from app.adapters.aksharamukha import AksharaAdapter
from app.core.config import settings

tamil_regex = re.compile(r"^[\u0B80-\u0BFF\s]+$")


class TransliterationService:
    def __init__(self):
        self.adapter = AksharaAdapter()

    async def transliterate(self, text: str, mode: str, limit: int, request_id: str = "n/a") -> List[dict]:
        text = (text or "").strip()
        if not text or len(text) > settings.MAX_TEXT_LEN:
            logging.warning("[IME] request_id=%s invalid_input len=%d", request_id, len(text))
            return []
        limit = max(1, min(limit or 8, 12))

        try:
            outputs = await self.adapter.transliterate(text, mode)
            logging.info("[AKSHARA] request_id=%s ok outputs=%d", request_id, len(outputs))
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
        return suggestions

