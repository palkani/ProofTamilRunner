"""
Layer D: Frequency/Dataset Ranking.

Uses a local frequency dictionary to boost common Tamil words.
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional
from app.suggestion_engine.types import Candidate

logger = logging.getLogger(__name__)

# Maximum frequency boost
MAX_FREQ_BOOST = 0.12


class FrequencyRankerLayer:
    """Layer D: Frequency-based ranking boost."""

    def __init__(self, frequency_file: Optional[str] = None):
        """
        Initialize frequency ranker.
        
        Args:
            frequency_file: Path to frequency JSON file. If None, uses default.
        """
        self.frequency_dict: Dict[str, float] = {}
        self.max_freq = 0.0
        self._load_frequency_data(frequency_file)

    def _load_frequency_data(self, frequency_file: Optional[str] = None):
        """Load frequency data from file."""
        if frequency_file is None:
            # Default: look for data/ta_frequency_min.json
            base_dir = Path(__file__).parent.parent.parent
            frequency_file = base_dir / "data" / "ta_frequency_min.json"

        frequency_file = Path(frequency_file)

        if not frequency_file.exists():
            logger.warning(
                "frequency_file_not_found path=%s using_empty_dict", str(frequency_file)
            )
            return

        try:
            with open(frequency_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Expected format: {"word": frequency, ...} or [{"word": "...", "freq": ...}, ...]
            if isinstance(data, dict):
                self.frequency_dict = {k: float(v) for k, v in data.items()}
            elif isinstance(data, list):
                self.frequency_dict = {
                    item.get("word", ""): float(item.get("freq", item.get("frequency", 0)))
                    for item in data
                    if item.get("word")
                }

            if self.frequency_dict:
                self.max_freq = max(self.frequency_dict.values())
                logger.info(
                    "frequency_data_loaded words=%d max_freq=%.2f",
                    len(self.frequency_dict),
                    self.max_freq,
                )
            else:
                logger.warning("frequency_data_empty")

        except Exception as e:
            logger.error("frequency_data_load_error error=%s", str(e))
            self.frequency_dict = {}

    def apply_boost(self, candidates: List[Candidate]) -> List[Candidate]:
        """
        Apply frequency boost to candidates.
        
        Boost formula:
        freq_boost = (word_freq / max_freq) * MAX_FREQ_BOOST
        """
        if not self.frequency_dict or not self.max_freq:
            return candidates

        boosted: List[Candidate] = []

        for cand in candidates:
            word = cand.word
            freq_boost = 0.0

            # Exact word match
            if word in self.frequency_dict:
                freq = self.frequency_dict[word]
                freq_boost = (freq / self.max_freq) * MAX_FREQ_BOOST

            # Prefix match (for partial words)
            elif len(word) >= 2:
                # Check if any word in dict starts with this prefix
                prefix_matches = [
                    freq
                    for dict_word, freq in self.frequency_dict.items()
                    if dict_word.startswith(word)
                ]
                if prefix_matches:
                    # Use average frequency of matches
                    avg_freq = sum(prefix_matches) / len(prefix_matches)
                    freq_boost = (avg_freq / self.max_freq) * MAX_FREQ_BOOST * 0.5

            new_score = min(1.0, cand.base_score + freq_boost)

            boosted.append(
                Candidate(
                    word=word,
                    base_score=new_score,
                    source_layer=cand.source_layer,
                    debug={
                        **(cand.debug or {}),
                        "freq_boost": freq_boost,
                        "has_freq": word in self.frequency_dict,
                    },
                )
            )

        return boosted

