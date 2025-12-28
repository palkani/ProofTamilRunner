"""
Tamil Unicode normalization and utility functions.
"""

import unicodedata
import re
from typing import Set, Tuple, Optional, Dict

# Tamil Unicode block: U+0B80 to U+0BFF
TAMIL_START = 0x0B80
TAMIL_END = 0x0BFF

# Tamil dependent vowels (vowel signs that attach to consonants)
DEPENDENT_VOWELS: Set[str] = {
    '\u0BBE',  # ா (aa)
    '\u0BBF',  # ி (i)
    '\u0BC0',  # ீ (ii)
    '\u0BC1',  # ு (u)
    '\u0BC2',  # ூ (uu)
    '\u0BC6',  # ெ (e)
    '\u0BC7',  # ே (ee)
    '\u0BC8',  # ை (ai)
    '\u0BCA',  # ொ (o)
    '\u0BCB',  # ோ (oo)
    '\u0BCC',  # ௌ (au)
}

# Pulli (virama) - removes inherent vowel
PULLI = '\u0BCD'  # ்

# Tamil consonants (base forms)
TAMIL_CONSONANTS: Set[str] = {
    '\u0B95', '\u0B99', '\u0B9A', '\u0B9E', '\u0B9F', '\u0BA3', '\u0BA4', '\u0BA8',
    '\u0BAA', '\u0BAE', '\u0BAF', '\u0BB0', '\u0BB2', '\u0BB5', '\u0BB4', '\u0BB3',
    '\u0BB1', '\u0BA9', '\u0B95', '\u0B9C', '\u0B9F', '\u0BA4', '\u0BAA',
    '\u0B95', '\u0B9A', '\u0B9F', '\u0BA4', '\u0BAA',  # duplicates for completeness
}

# Common Tamil consonants mapping (roman -> Tamil)
ROMAN_TO_TAMIL_CONSONANT: Dict[str, str] = {
    'k': '\u0B95',  # க
    'g': '\u0B95',  # க (no voiced distinction in Tamil)
    'c': '\u0B9A',  # ச
    'j': '\u0B9C',  # ஜ
    's': '\u0B9A',  # ச
    't': '\u0BA4',  # த
    'd': '\u0BA4',  # த
    'n': '\u0BA8',  # ந
    'p': '\u0BAA',  # ப
    'b': '\u0BAA',  # ப
    'm': '\u0BAE',  # ம
    'y': '\u0BAF',  # ய
    'r': '\u0BB0',  # ர
    'l': '\u0BB2',  # ல
    'v': '\u0BB5',  # வ
    'w': '\u0BB5',  # வ
    'z': '\u0BB4',  # ழ
    'L': '\u0BB3',  # ள
    'R': '\u0BB1',  # ற
    'N': '\u0BA9',  # ன
    'h': '\u0BB9',  # ஹ
    'f': '\u0BAA',  # ப (approximation)
}


def normalize_unicode(text: str) -> str:
    """Normalize text to NFC (Canonical Composition)."""
    return unicodedata.normalize('NFC', text)


def is_tamil_char(ch: str) -> bool:
    """Check if character is in Tamil Unicode block."""
    if not ch:
        return False
    code = ord(ch[0])
    return TAMIL_START <= code <= TAMIL_END


def is_tamil_text(text: str) -> bool:
    """Check if text contains only Tamil characters and whitespace."""
    if not text:
        return False
    for ch in text:
        if ch.isspace():
            continue
        if not is_tamil_char(ch):
            return False
    return True


def is_dependent_vowel(ch: str) -> bool:
    """Check if character is a Tamil dependent vowel."""
    return ch in DEPENDENT_VOWELS


def is_pulli(ch: str) -> bool:
    """Check if character is pulli (virama)."""
    return ch == PULLI


def is_consonant(ch: str) -> bool:
    """Check if character is a Tamil consonant."""
    return ch in TAMIL_CONSONANTS


def get_consonant_base(roman: str) -> Optional[str]:
    """Get Tamil consonant base for roman letter (single char, lowercase)."""
    return ROMAN_TO_TAMIL_CONSONANT.get(roman.lower())


def has_invalid_vowel_sequence(word: str) -> bool:
    """
    Check if a Tamil word has invalid vowel sequences.
    Two dependent vowels in a row is linguistically invalid.
    """
    if not word or len(word) < 2:
        return False

    for i in range(1, len(word)):
        prev = word[i - 1]
        curr = word[i]

        # Two dependent vowels in a row is invalid
        if prev in DEPENDENT_VOWELS and curr in DEPENDENT_VOWELS:
            return True

    return False


def is_valid_tamil_word(word: str) -> bool:
    """
    Basic validation for Tamil word structure.
    - Must be Tamil characters only
    - No invalid vowel sequences
    - No Latin/digits
    """
    if not word:
        return False

    # Check for Latin/digits
    if any(c.isascii() and c.isalnum() for c in word):
        return False

    # Check for invalid vowel sequences
    if has_invalid_vowel_sequence(word):
        return False

    return True


def extract_tamil_boundary(context: str, cursor: int) -> Tuple[str, str, bool]:
    """
    Extract left and right context around cursor, and detect if at word boundary.
    
    Returns:
        (left_context, right_context, is_at_boundary)
    """
    if not context:
        return "", "", True

    cursor = max(0, min(cursor, len(context)))

    # Extract left context (up to 50 chars before cursor)
    left_start = max(0, cursor - 50)
    left_context = context[left_start:cursor]

    # Extract right context (up to 20 chars after cursor)
    right_end = min(len(context), cursor + 20)
    right_context = context[cursor:right_end]

    # Detect boundary: whitespace, punctuation, or start/end
    is_at_boundary = (
        cursor == 0 or
        cursor == len(context) or
        context[cursor - 1].isspace() or
        not context[cursor - 1].isalnum()
    )

    return left_context, right_context, is_at_boundary


def get_last_tamil_char_class(context: str) -> Optional[str]:
    """
    Get the class of the last Tamil character in context.
    Returns: 'consonant', 'vowel', 'pulli', 'consonant_with_vowel', or None
    """
    if not context:
        return None

    # Find last Tamil character
    last_tamil_idx = -1
    for i in range(len(context) - 1, -1, -1):
        if is_tamil_char(context[i]):
            last_tamil_idx = i
            break

    if last_tamil_idx == -1:
        return None

    ch = context[last_tamil_idx]

    # Check if it's a dependent vowel
    if is_dependent_vowel(ch):
        # Check if there's a consonant before it
        if last_tamil_idx > 0 and is_consonant(context[last_tamil_idx - 1]):
            return 'consonant_with_vowel'
        return 'vowel'

    # Check if it's pulli
    if is_pulli(ch):
        return 'pulli'

    # Check if it's a consonant
    if is_consonant(ch):
        # Check if followed by vowel
        if last_tamil_idx < len(context) - 1 and is_dependent_vowel(context[last_tamil_idx + 1]):
            return 'consonant_with_vowel'
        return 'consonant'

    return None

