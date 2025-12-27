import logging
import math
import os
from functools import lru_cache

FREQ_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "data", "ta_freq.tsv")


def _load_freq():
    freq = {}
    freq_max = 0
    if not os.path.exists(FREQ_PATH):
        logging.warning("[FREQ] frequency file missing at %s", FREQ_PATH)
        return freq, 0
    try:
        with open(FREQ_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "\t" not in line:
                    continue
                word, val = line.split("\t", 1)
                try:
                    v = int(val)
                except ValueError:
                    continue
                freq[word] = v
                if v > freq_max:
                    freq_max = v
    except Exception as e:
        logging.error("[FREQ] failed to load freq dict: %s", e)
    return freq, freq_max


FREQ_DICT, FREQ_MAX = _load_freq()
BASELINE = 0.02


def freq_score(word: str) -> float:
    if not word:
        return BASELINE
    if not FREQ_DICT or FREQ_MAX <= 0:
        return BASELINE
    v = FREQ_DICT.get(word, 0)
    if v <= 0:
        return BASELINE
    # log-scaled score 0..1
    return min(1.0, math.log(1 + v) / math.log(1 + FREQ_MAX))


@lru_cache(maxsize=2048)
def has_freq(word: str) -> bool:
    return word in FREQ_DICT
