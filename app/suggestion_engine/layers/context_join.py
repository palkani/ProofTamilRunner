"""
Layer C: Context-Aware Completion (prefix-aware).

Detects if user is inside a Tamil word or at boundary, and applies joining rules.
"""

import logging
from typing import List, Optional, Tuple
from app.suggestion_engine.types import Candidate
from app.suggestion_engine.normalization import (
    extract_tamil_boundary,
    get_last_tamil_char_class,
    is_tamil_char,
    is_valid_tamil_word,
    normalize_unicode,
)

logger = logging.getLogger(__name__)


class ContextJoinLayer:
    """Layer C: Context-aware joining and completion."""

    def generate(
        self,
        q: str,
        candidates: List[Candidate],
        context: Optional[str] = None,
        cursor: Optional[int] = None,
    ) -> List[Candidate]:
        """
        Apply context-aware adjustments to candidates.
        
        - If at boundary: allow standalone syllables
        - If inside Tamil word: prefer candidates that can join
        - Adjust scores based on joinability
        """
        if not context or cursor is None:
            # No context: candidates remain as-is
            return candidates

        left_context, right_context, is_at_boundary = extract_tamil_boundary(
            context, cursor
        )
        last_char_class = get_last_tamil_char_class(left_context)

        adjusted: List[Candidate] = []

        for cand in candidates:
            word = cand.word
            base_score = cand.base_score
            context_boost = 0.0

            if is_at_boundary:
                # At boundary: standalone is fine, slight boost for common morphemes
                if len(word) <= 2:
                    context_boost = 0.02
            else:
                # Inside word: check if can join
            if last_char_class:
                # Check if candidate can join with last Tamil char
                if self._can_join(left_context, word, last_char_class):
                    context_boost = 0.05
                else:
                    # Small penalty for candidates that can't join (but don't exclude)
                    context_boost = -0.01

            # Clamp final score
            new_score = max(0.0, min(1.0, base_score + context_boost))

            adjusted.append(
                Candidate(
                    word=word,
                    base_score=new_score,
                    source_layer=cand.source_layer,
                    debug={
                        **(cand.debug or {}),
                        "context_boost": context_boost,
                        "at_boundary": is_at_boundary,
                        "last_char_class": last_char_class,
                    },
                )
            )

        return adjusted

    def _can_join(self, left_context: str, candidate: str, last_char_class: str) -> bool:
        """
        Check if candidate can join with the last Tamil character in context.
        
        Rules:
        - If last char is consonant with pulli, candidate can join
        - If last char is consonant without vowel, candidate can join
        - If last char ends with vowel, candidate should start with consonant
        """
        if not left_context or not candidate:
            return False

        # Find last Tamil char in context
        last_tamil_idx = -1
        for i in range(len(left_context) - 1, -1, -1):
            if is_tamil_char(left_context[i]):
                last_tamil_idx = i
                break

        if last_tamil_idx == -1:
            return False

        last_char = left_context[last_tamil_idx]

        # Check if last char can be followed by candidate
        if last_char_class == "pulli" or last_char_class == "consonant":
            # Can join: consonant/pulli can be followed by vowel or consonant
            return True

        if last_char_class == "consonant_with_vowel":
            # Check if candidate starts with consonant (can continue word)
            if candidate and is_tamil_char(candidate[0]):
                # Check if it's a consonant (not a vowel)
                from app.suggestion_engine.normalization import is_consonant
                if is_consonant(candidate[0]):
                    return True

        return False

