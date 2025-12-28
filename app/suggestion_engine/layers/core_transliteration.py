"""
Layer A: Core Transliteration (strict).

Uses existing transliteration mechanism to generate strict transliterations.
For single consonant inputs, ensures both base consonant and consonant + pulli.
"""

import logging
from typing import List, Optional
from app.suggestion_engine.types import Candidate
from app.suggestion_engine.normalization import (
    get_consonant_base,
    is_valid_tamil_word,
    normalize_unicode,
)
from app.adapters.aksharamukha import AksharaAdapter
from app.clients.transliterator_client import build_client
from app.core.config import settings

logger = logging.getLogger(__name__)


class CoreTransliterationLayer:
    """Layer A: Core strict transliteration."""

    def __init__(self, adapter: Optional[AksharaAdapter] = None, client=None):
        self.adapter = adapter or AksharaAdapter()
        self.client = client or build_client()
        self.runner_enabled = settings.TRANSLITERATOR_ENABLED

    async def generate(
        self, q: str, mode: str, request_id: str = "n/a"
    ) -> List[Candidate]:
        """
        Generate core transliteration candidates.
        
        For single consonant inputs (like "m", "k", "t"), ensures:
        - base consonant (ம)
        - consonant + pulli (ம்)
        """
        candidates: List[Candidate] = []
        q_lower = q.lower().strip()

        if not q_lower:
            return candidates

        # Try external runner first if enabled
        runner_candidates = []
        if self.runner_enabled and self.client:
            try:
                data = await self.client.transliterate(q)
                outputs = [
                    s.get("word") or s.get("ta")
                    for s in data.get("suggestions", [])
                    if s
                ]
                for out in outputs:
                    if out and is_valid_tamil_word(out):
                        runner_candidates.append(
                            Candidate(
                                word=normalize_unicode(out),
                                base_score=0.95,
                                source_layer="core_runner",
                                debug={"runner": True},
                            )
                        )
            except Exception as e:
                logger.warning(
                    "core_transliteration_runner_failed request_id=%s error=%s",
                    request_id,
                    str(e),
                )

        # Fallback to adapter
        adapter_candidates = []
        if not runner_candidates:
            try:
                outputs = await self.adapter.transliterate(q, mode)
                for out in outputs:
                    if out and is_valid_tamil_word(out):
                        adapter_candidates.append(
                            Candidate(
                                word=normalize_unicode(out),
                                base_score=0.90,
                                source_layer="core_adapter",
                                debug={"runner": False},
                            )
                        )
            except Exception as e:
                logger.warning(
                    "core_transliteration_adapter_failed request_id=%s error=%s",
                    request_id,
                    str(e),
                )

        # Combine runner and adapter results
        candidates.extend(runner_candidates)
        candidates.extend(adapter_candidates)

        # For single consonant inputs, ensure base + pulli forms
        if len(q_lower) == 1 and q_lower.isalpha():
            consonant_base = get_consonant_base(q_lower)
            if consonant_base:
                # Base consonant
                base_candidate = Candidate(
                    word=consonant_base,
                    base_score=0.85,
                    source_layer="core_consonant_base",
                    debug={"consonant": True},
                )
                candidates.append(base_candidate)

                # Consonant + pulli
                pulli_candidate = Candidate(
                    word=consonant_base + "\u0BCD",  # ்
                    base_score=0.88,
                    source_layer="core_consonant_pulli",
                    debug={"consonant": True, "pulli": True},
                )
                candidates.append(pulli_candidate)

        # Deduplicate by word (keep highest score)
        seen = {}
        for cand in candidates:
            word = cand.word
            if word not in seen or seen[word].base_score < cand.base_score:
                seen[word] = cand

        return list(seen.values())

