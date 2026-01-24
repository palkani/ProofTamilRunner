"""
Postgres-backed corpus loader for ProofTamilRunner IME.

Loads these tables (created by the main Go backend):
- tamil_words(tamil_text, transliteration, alternate_spellings, frequency, user_confirmed)
- tamil_phrases(phrase, frequency)
- tamil_bigrams(word, next_word, frequency)

Goal:
- Deterministic, high-quality suggestions for common words and colloquial spellings
  by using stored transliterations/alternate spellings + frequencies.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from bisect import bisect_left
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:
    import psycopg2  # type: ignore
except Exception:  # pragma: no cover
    psycopg2 = None

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WordEntry:
    tamil: str
    freq: int
    confirmed: int


class CorpusIndex:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loaded = False

        # roman_key -> list[WordEntry] (top few entries)
        self._word_map: Dict[str, List[WordEntry]] = {}
        self._word_keys: List[str] = []

        # tamil_first_token -> list[(phrase, freq)]
        self._phrase_by_first: Dict[str, List[Tuple[str, int]]] = {}

        # prev_tamil -> next_tamil -> freq
        self._bigrams: Dict[str, Dict[str, int]] = {}

        self._source = "empty"

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def source(self) -> str:
        return self._source

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._load()
            self._loaded = True

    def _load(self) -> None:
        if not settings.CORPUS_ENABLED:
            self._source = "disabled"
            logger.info("[CORPUS] disabled (CORPUS_ENABLED=false)")
            return
        if psycopg2 is None:
            self._source = "missing_driver"
            logger.warning("[CORPUS] psycopg2 not installed; corpus suggestions disabled")
            return
        if not (settings.DATABASE_URL or "").strip():
            self._source = "no_db_url"
            logger.warning("[CORPUS] DATABASE_URL not set; corpus suggestions disabled")
            return

        try:
            conn = psycopg2.connect(settings.DATABASE_URL)
        except Exception as e:
            self._source = "db_connect_error"
            logger.warning("[CORPUS] failed to connect to Postgres: %s", str(e))
            return

        try:
            with conn.cursor() as cur:
                # Words
                cur.execute(
                    """
                    SELECT tamil_text, transliteration, alternate_spellings, frequency, user_confirmed
                    FROM tamil_words
                    WHERE deleted_at IS NULL
                    ORDER BY frequency DESC
                    LIMIT %s
                    """,
                    (settings.CORPUS_TOP_K,),
                )
                rows = cur.fetchall()

                word_map: Dict[str, List[WordEntry]] = {}
                for tamil_text, translit, alt_json, freq, confirmed in rows:
                    t = (tamil_text or "").strip()
                    if not t:
                        continue
                    f = int(freq or 0)
                    c = int(confirmed or 0)
                    entry = WordEntry(tamil=t, freq=f, confirmed=c)

                    keys: List[str] = []
                    if translit:
                        keys.append(str(translit).strip().lower())
                    if alt_json:
                        try:
                            arr = json.loads(alt_json) if isinstance(alt_json, str) else []
                            if isinstance(arr, list):
                                for x in arr:
                                    if isinstance(x, str) and x.strip():
                                        keys.append(x.strip().lower())
                        except Exception:
                            pass

                    for k in keys:
                        if not k:
                            continue
                        bucket = word_map.get(k)
                        if bucket is None:
                            bucket = []
                            word_map[k] = bucket
                        bucket.append(entry)

                # Keep only top few per key for memory + speed
                for k, bucket in word_map.items():
                    bucket.sort(key=lambda e: (-(e.freq + e.confirmed * 2), e.tamil))
                    word_map[k] = bucket[:10]

                keys_sorted = sorted(word_map.keys())

                # Phrases
                cur.execute(
                    """
                    SELECT phrase, frequency
                    FROM tamil_phrases
                    ORDER BY frequency DESC
                    LIMIT %s
                    """,
                    (settings.CORPUS_PHRASE_TOP_K,),
                )
                phrase_rows = cur.fetchall()
                phrase_by_first: Dict[str, List[Tuple[str, int]]] = {}
                for ph, pf in phrase_rows:
                    p = (ph or "").strip()
                    if not p:
                        continue
                    first = p.split(" ", 1)[0]
                    bucket = phrase_by_first.get(first)
                    if bucket is None:
                        bucket = []
                        phrase_by_first[first] = bucket
                    bucket.append((p, int(pf or 0)))
                for k, bucket in phrase_by_first.items():
                    bucket.sort(key=lambda x: (-x[1], x[0]))
                    phrase_by_first[k] = bucket[:20]

                # Bigrams
                cur.execute(
                    """
                    SELECT word, next_word, frequency
                    FROM tamil_bigrams
                    ORDER BY frequency DESC
                    LIMIT %s
                    """,
                    (settings.CORPUS_BIGRAM_TOP_K,),
                )
                big_rows = cur.fetchall()
                big: Dict[str, Dict[str, int]] = {}
                for w, n, bf in big_rows:
                    w2 = (w or "").strip()
                    n2 = (n or "").strip()
                    if not w2 or not n2:
                        continue
                    inner = big.get(w2)
                    if inner is None:
                        inner = {}
                        big[w2] = inner
                    inner[n2] = max(int(bf or 0), inner.get(n2, 0))

                self._word_map = word_map
                self._word_keys = keys_sorted
                self._phrase_by_first = phrase_by_first
                self._bigrams = big
                self._source = "postgres"
                logger.info(
                    "[CORPUS] loaded word_keys=%d phrases=%d bigram_rows=%d",
                    len(keys_sorted),
                    len(phrase_rows),
                    len(big_rows),
                )
        except Exception as e:
            self._source = "db_query_error"
            logger.warning("[CORPUS] load failed: %s", str(e))
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def suggest_words(self, roman_q: str, limit: int, prev_tamil: Optional[str] = None) -> List[Tuple[str, float]]:
        self.ensure_loaded()
        if not self._word_keys:
            return []

        variants = generate_roman_variants(roman_q)
        if not variants:
            return []

        candidates: Dict[str, float] = {}
        big = self._bigrams.get(prev_tamil or "", {}) if prev_tamil else {}

        for v in variants:
            for key in self._iter_keys_with_prefix(v, max_keys=250):
                bucket = self._word_map.get(key) or []
                for e in bucket:
                    base = math.log1p(max(0, e.freq)) + 0.25 * math.log1p(max(0, e.confirmed))
                    tight = min(1.0, len(v) / max(1, len(key)))
                    score = base + 0.5 * tight
                    if big and e.tamil in big:
                        score += 0.35 * math.log1p(big.get(e.tamil, 0))
                    prev_score = candidates.get(e.tamil)
                    if prev_score is None or score > prev_score:
                        candidates[e.tamil] = score
                if len(candidates) >= 600:
                    break
            if len(candidates) >= 600:
                break

        if not candidates:
            return []

        ranked = sorted(candidates.items(), key=lambda x: (-x[1], x[0]))[: max(10, limit)]
        max_score = ranked[0][1] if ranked else 1.0
        out = [(w, round((s / max_score), 2) if max_score > 0 else 0.5) for w, s in ranked]
        if out:
            out[0] = (out[0][0], 1.0)
        return out[:limit]

    def suggest_phrases(self, first_word: str, limit: int) -> List[Tuple[str, float]]:
        self.ensure_loaded()
        if not first_word:
            return []
        bucket = self._phrase_by_first.get(first_word)
        if not bucket:
            return []
        ranked = bucket[: max(10, limit)]
        maxf = ranked[0][1] if ranked else 1
        out: List[Tuple[str, float]] = []
        for idx, (ph, f) in enumerate(ranked):
            sc = (math.log1p(f) / math.log1p(maxf)) if maxf > 0 else 0.5
            sc = round(sc, 2)
            if idx == 0:
                sc = 1.0
            out.append((ph, sc))
            if len(out) >= limit:
                break
        return out

    def _iter_keys_with_prefix(self, prefix: str, max_keys: int = 200):
        if not prefix:
            return
        keys = self._word_keys
        lo = bisect_left(keys, prefix)
        hi = bisect_left(keys, prefix + "\uffff")
        count = 0
        for i in range(lo, min(hi, len(keys))):
            yield keys[i]
            count += 1
            if count >= max_keys:
                break


_CORPUS: Optional[CorpusIndex] = None


def get_corpus() -> CorpusIndex:
    global _CORPUS
    if _CORPUS is None:
        _CORPUS = CorpusIndex()
    return _CORPUS


_VOWELS = set("aeiou")
_DOUBLE_CANDIDATES = set("pktc")


def generate_roman_variants(q: str) -> List[str]:
    q = (q or "").strip().lower()
    if not q:
        return []

    out: List[str] = [q]

    # Single-doubling variants for common Tamil transliteration patterns:
    # sapadu -> sappadu
    for i in range(1, len(q) - 1):
        ch = q[i]
        if ch not in _DOUBLE_CANDIDATES:
            continue
        prev = q[i - 1]
        nxt = q[i + 1]
        if prev in _VOWELS and nxt in _VOWELS:
            out.append(q[:i] + ch + q[i:])

    # Common typo: missing extra 'a' in long-vowel positions (saappaadu vs sappadu)
    if "aa" not in q:
        # only try one insertion to keep bounded
        for i in range(1, len(q)):
            if q[i - 1] == "a" and q[i] not in _VOWELS:
                out.append(q[:i] + "a" + q[i:])
                break

    # Dedupe and cap
    dedup: List[str] = []
    seen = set()
    for x in out:
        if x and x not in seen:
            seen.add(x)
            dedup.append(x)
        if len(dedup) >= 12:
            break
    return dedup


