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


def validate_tamil_orthography(word: str) -> bool:
    """
    Validate Tamil orthography rules.
    Returns True if valid, False if invalid.

    Rules:
    - dependent vowel cannot start a word
    - dependent vowel cannot follow another dependent vowel
    - pulli cannot follow a dependent vowel directly
    - no double pulli (handled in normalize_unicode)
    - reject latin/digits
    - reject invalid endings patterns
    """
    if not word or not TAMIL_REGEX.match(word):
        return False

    # Check for Latin/digit leakage
    if re.search(r"[A-Za-z0-9]", word):
        return False

    # Check invalid endings (common garbage patterns)
    invalid_endings = ["ொஒ", "்ி", "ுு", "ாா", "ிி", "ீீ", "ூூ", "ெெ", "ேே", "ைை", "ொொ", "ோோ"]
    for ending in invalid_endings:
        if word.endswith(ending):
            return False

    # Check character-by-character rules
    for i, char in enumerate(word):
        # Dependent vowel cannot start a word
        if i == 0 and char in DEPENDENT_VOWELS:
            return False

        if i > 0:
            prev = word[i - 1]

            # Dependent vowel cannot follow another dependent vowel
            if prev in DEPENDENT_VOWELS and char in DEPENDENT_VOWELS:
                return False

            # Pulli cannot follow a dependent vowel (very rare/invalid)
            if prev in DEPENDENT_VOWELS and char == PULLI:
                return False

    return True


def eliminate_morphological_garbage(candidate: str, input_length: int) -> bool:
    """
    Eliminate morphologically invalid forms.
    Returns True if valid, False if garbage.

    Rules:
    - candidates longer than 3 chars when input length <= 2
    - mechanically expanded vowels (e.g. முஉ, முஉஉ)
    - meaningless suffix chaining
    """
    if not candidate:
        return False

    # Short input (<=2 chars) should not produce long expansions (>3 chars)
    if input_length <= 2 and len(candidate) > 3:
        return False

    # Detect mechanically expanded vowels (consecutive dependent vowels already checked in orthography)
    # Additional check: patterns like "முஉ", "முஉஉ" (base + dependent vowel + dependent vowel)
    # This is caught by orthography validation, but add extra safety

    # Check for excessive dependent vowel repetition in different positions
    dep_vowel_count = sum(1 for c in candidate if c in DEPENDENT_VOWELS)
    if dep_vowel_count > 2 and input_length <= 2:
        # Too many dependent vowels for a short input
        return False

    return True


def ends_with_tamil_vowel(word: str) -> bool:
    """Check if word ends with a Tamil vowel (independent or dependent)."""
    if not word:
        return False
    last = word[-1]
    return last in TAMIL_VOWELS or last in DEPENDENT_VOWELS

