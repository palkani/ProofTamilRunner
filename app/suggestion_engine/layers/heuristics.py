"""
Layer E: Phonetic/Heuristic Neighbors (smart mode only).

Adds limited heuristic candidates for "spoken" typing patterns.
All heuristics are configurable and unit-tested.
"""

import logging
from typing import List, Dict, Optional
from app.suggestion_engine.types import Candidate
from app.suggestion_engine.normalization import (
    get_consonant_base,
    is_valid_tamil_word,
    normalize_unicode,
)

logger = logging.getLogger(__name__)

# Heuristic mappings: roman -> [Tamil alternatives]
# These are phonetic/orthographic neighbors
HEURISTIC_NEIGHBORS: Dict[str, List[str]] = {
    "m": ["\u0BA9"],  # ன (nasal neighbor)
    "n": ["\u0BAE"],  # ம (nasal neighbor)
    "t": ["\u0B9F", "\u0BA4"],  # ட, த
    "d": ["\u0B9F", "\u0BA4"],  # ட, த
    "k": ["\u0B95"],  # க
    "g": ["\u0B95"],  # க
    "p": ["\u0BAA"],  # ப
    "b": ["\u0BAA"],  # ப
    "r": ["\u0BB0", "\u0BB1"],  # ர, ற
    "l": ["\u0BB2", "\u0BB3"],  # ல, ள
    "y": ["\u0BAF"],  # ய
    "v": ["\u0BB5"],  # வ
    "w": ["\u0BB5"],  # வ
}

# Base score for heuristic candidates (lower than core)
HEURISTIC_BASE_SCORE = 0.40


class HeuristicsLayer:
    """Layer E: Heuristic neighbors (smart mode only)."""

    def __init__(self, enabled: bool = True):
        """
        Initialize heuristics layer.
        
        Args:
            enabled: Whether heuristics are enabled (default: True for smart mode)
        """
        self.enabled = enabled

    def generate(self, q: str, mode: str) -> List[Candidate]:
        """
        Generate heuristic candidates.
        
        Only applies in "smart" mode and when enabled.
        """
        candidates: List[Candidate] = []

        if mode != "smart" or not self.enabled:
            return candidates

        q_lower = q.lower().strip()

        # Only for single consonant inputs
        if len(q_lower) != 1 or not q_lower.isalpha():
            return candidates

        # Get heuristic neighbors
        neighbors = HEURISTIC_NEIGHBORS.get(q_lower, [])

        for neighbor in neighbors:
            if is_valid_tamil_word(neighbor):
                candidates.append(
                    Candidate(
                        word=normalize_unicode(neighbor),
                        base_score=HEURISTIC_BASE_SCORE,
                        source_layer="heuristics",
                        debug={"heuristic": True, "original": q_lower},
                    )
                )

        return candidates

