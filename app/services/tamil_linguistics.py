"""
Tamil linguistic validation and normalization utilities.
Production-grade implementation for ProofTamilRunner suggest API.
"""

import re
import unicodedata
from typing import List, Set


# Tamil Unicode ranges
TAMIL_START = 0x0B80
TAMIL_END = 0x0BFF
TAMIL_REGEX = re.compile(r"^[\u0B80-\u0BFF\s]+$")

# Tamil independent vowels (uyir)
TAMIL_VOWELS = set("அஆஇஈஉஊஎஏஐஒஓஔ")

# Tamil dependent vowels (uyirmei - vowel signs)
DEPENDENT_VOWELS: Set[str] = {
    "ா",  # aa
    "ி",  # i
    "ீ",  # ii
    "ு",  # u
    "ூ",  # uu
    "ெ",  # e
    "ே",  # ee
    "ை",  # ai
    "ொ",  # o
    "ோ",  # oo
    "ௌ",  # au
}

# Pulli (virama - pure consonant marker)
PULLI = "்"


def normalize_roman_input(text: str) -> str:
    """
    Normalize Roman input for transliteration.
    - lowercase
    - trim whitespace
    - collapse repeated letters (max 2)
    - normalize common variants
    - remove non-alphabetic chars (except apostrophe)
    """
    if not text:
        return ""

    # lowercase and trim
    normalized = text.lower().strip()

    # Normalize variants (deterministic)
    variant_map = {
        "thamizh": "tamil",
        "thamiz": "tamil",
        "tamizh": "tamil",
        "tamiz": "tamil",
    }
    for variant, canonical in variant_map.items():
        if normalized == variant or normalized.startswith(variant + " "):
            normalized = canonical + normalized[len(variant) :]
            break

    # Collapse repeated letters (max 2 consecutive)
    normalized = re.sub(r"(.)\1{2,}", r"\1\1", normalized)

    # Remove non-alphabetic chars (except apostrophe)
    normalized = re.sub(r"[^a-z']", "", normalized)

    return normalized


def normalize_unicode(candidate: str) -> str:
    """
    Normalize Unicode for Tamil candidates.
    - NFC normalization
    - Collapse repeated pulli
    - Strip whitespace
    """
    if not candidate:
        return ""

    # NFC normalization
    normalized = unicodedata.normalize("NFC", candidate)

    # Collapse repeated pulli (multiple pulli in a row is invalid)
    normalized = re.sub(r"்{2,}", PULLI, normalized)

    # Strip whitespace
    normalized = normalized.strip()

    return normalized


def is_structurally_invalid_tamil(word: str) -> bool:
    """
    PART B: Minimal hard filter - reject ONLY structurally impossible Tamil.
    Returns True if invalid (should be rejected), False if valid.
    
    Hard rejections:
    - Contains consecutive dependent vowel signs
    - Contains dependent vowel followed by independent vowel (e.g., "மு" + "உ" -> "முஉ")
    - Contains independent vowel followed by dependent vowel (e.g., "அ" + "ா")
    - Dependent vowel sign without base consonant (starts with vowel sign)
    - Contains Latin/digits (leakage)
    - Contains repeated pulli (்்) - already handled in normalize_unicode but check here too
    - Invalid Unicode ordering (not pure Tamil)
    """
    if not word:
        return True
    
    # Reject non-Tamil characters (Latin/digit leakage)
    if not TAMIL_REGEX.match(word):
        return True
    
    # Check for Latin/digit leakage
    if re.search(r"[A-Za-z0-9]", word):
        return True

    # Check character-by-character structural rules
    independent_vowels = set("அஆஇஈஉஊஎஏஐஒஓஔ")
    for i, char in enumerate(word):
        # Dependent vowel cannot start a word (no base consonant)
        if i == 0 and char in DEPENDENT_VOWELS:
            return True

        if i > 0:
            prev = word[i - 1]

            # Consecutive dependent vowels (structurally impossible)
            if prev in DEPENDENT_VOWELS and char in DEPENDENT_VOWELS:
                return True

            # Dependent vowel followed by independent vowel is invalid (e.g., "மு" + "உ" -> "முஉ")
            if prev in DEPENDENT_VOWELS and char in independent_vowels:
                return True

            # Independent vowel followed by dependent vowel is invalid (e.g., "அ" + "ா")
            if prev in independent_vowels and char in DEPENDENT_VOWELS:
                return True

            # Double pulli (structurally invalid)
            if prev == PULLI and char == PULLI:
                return True

    return False


def validate_tamil_orthography(word: str) -> bool:
    """
    DEPRECATED: Use is_structurally_invalid_tamil instead.
    Kept for backward compatibility but delegates to new function.
    """
    return not is_structurally_invalid_tamil(word)


def morphology_score(candidate: str, input_length: int) -> float:
    """
    PART C: Soft morphology scoring (penalties, not rejection).
    Returns score between 0.0 and 1.0.
    
    Penalizes but never rejects:
    - Unusually long expansions for short inputs
    - Excessive dependent vowel usage
    - Odd suffix patterns
    """
    if not candidate:
        return 0.0
    
    score = 1.0
    
    # Penalize long expansions for short inputs (soft penalty)
    if input_length <= 2 and len(candidate) > 3:
        # Reduce score but don't reject
        penalty = min(0.4, (len(candidate) - 3) * 0.1)
        score -= penalty
    
    # Penalize excessive dependent vowel usage (soft)
    dep_vowel_count = sum(1 for c in candidate if c in DEPENDENT_VOWELS)
    if dep_vowel_count > 3:
        score -= 0.2
    
    # Prefer reasonable lengths
    if len(candidate) > 12:
        score -= 0.3
    
    return max(0.0, score)


def eliminate_morphological_garbage(candidate: str, input_length: int) -> bool:
    """
    Hard morphology gate used by tests and as a safety net.

    Rules (simple + conservative):
    - For very short Roman inputs (<=2 chars), do not allow long Tamil outputs (>3 chars).
    - For short inputs (<=4 chars), do not allow extremely long outputs (>8 chars).
    """
    if not candidate:
        return False
    if input_length <= 2 and len(candidate) > 3:
        return False
    if input_length <= 4 and len(candidate) > 8:
        return False
    return True


def ends_with_tamil_vowel(word: str) -> bool:
    """Check if word ends with a Tamil vowel (independent or dependent)."""
    if not word:
        return False
    last = word[-1]
    return last in TAMIL_VOWELS or last in DEPENDENT_VOWELS

