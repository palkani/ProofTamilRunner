"""
Layer B: Tamil Orthography Expansion (vowel matrix).

Generates syllabic expansions using vowel markers for consonant-only fragments.
"""

import logging
from typing import List
from app.suggestion_engine.types import Candidate
from app.suggestion_engine.normalization import (
    get_consonant_base,
    is_valid_tamil_word,
    normalize_unicode,
    DEPENDENT_VOWELS,
)

logger = logging.getLogger(__name__)

# Vowel expansions with their roman equivalents and scores
# Format: (dependent_vowel, roman_hint, base_score)
VOWEL_EXPANSIONS = [
    ("\u0BBE", "aa", 0.75),  # ா
    ("\u0BBF", "i", 0.70),   # ி
    ("\u0BC0", "ii", 0.65),  # ீ
    ("\u0BC1", "u", 0.70),   # ு
    ("\u0BC2", "uu", 0.65),  # ூ
    ("\u0BC6", "e", 0.72),   # ெ
    ("\u0BC7", "ee", 0.68),  # ே
    ("\u0BC8", "ai", 0.60),  # ை
    ("\u0BCA", "o", 0.70),   # ொ
    ("\u0BCB", "oo", 0.65),  # ோ
    ("\u0BCC", "au", 0.55),  # ௌ
]


class TamilVowelExpandLayer:
    """Layer B: Tamil vowel expansion for consonant fragments."""

    def generate(self, q: str, core_candidates: List[Candidate]) -> List[Candidate]:
        """
        Generate vowel expansions for consonant-only fragments.
        
        Applies when:
        - q is a single consonant OR
        - last token is consonant boundary
        """
        candidates: List[Candidate] = []
        q_lower = q.lower().strip()

        # Only expand for single consonant inputs
        if len(q_lower) != 1 or not q_lower.isalpha():
            return candidates

        consonant_base = get_consonant_base(q_lower)
        if not consonant_base:
            return candidates

        # Generate expansions with each dependent vowel
        for vowel, roman_hint, base_score in VOWEL_EXPANSIONS:
            expanded = consonant_base + vowel
            if is_valid_tamil_word(expanded):
                candidates.append(
                    Candidate(
                        word=normalize_unicode(expanded),
                        base_score=base_score,
                        source_layer="vowel_expand",
                        debug={"vowel": vowel, "roman_hint": roman_hint},
                    )
                )

        return candidates

