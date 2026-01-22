"""
Canonical override map for common Tamil transliterations.
These bypass Aksharamukha for guaranteed correct outputs.
"""

from typing import Tuple

# Canonical mappings: roman_input -> tamil_output
CANONICAL_MAP = {
    "tamil": "தமிழ்",
    "thamizh": "தமிழ்",
    "thamiz": "தமிழ்",
    "tamizh": "தமிழ்",
    "tamiz": "தமிழ்",
    # Very common function words (strict - must be correct)
    "enna": "என்ன",
    "namma": "நம்ம",
    "enathu": "எனது",
    "enadu": "எனது",
    "enadhu": "எனது",
    # South
    "therkku": "தெற்கு",
    "therku": "தெற்கு",
    "therkk": "தெற்கு",
    "vanakkam": "வணக்கம்",
    "murugan": "முருகன்",
    "muruga": "முருகா",
    "naan": "நான்",
    "enakku": "எனக்கு",
    "mu": "மு",
    # Add more as needed
}


def get_canonical(tamil_input: str) -> Tuple[str, bool]:
    """
    Get canonical mapping if exists.
    Returns (tamil_output, found)
    """
    normalized = tamil_input.lower().strip()
    if normalized in CANONICAL_MAP:
        return CANONICAL_MAP[normalized], True
    return "", False

