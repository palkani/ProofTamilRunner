"""
Thread-safe in-memory LRU cache with TTL, suitable for Cloud Run instances.
"""
import time
import threading
import hashlib
from collections import OrderedDict
from typing import Any, Optional


class LRUCache:
    def __init__(self, max_size: int = 5000, default_ttl: int = 600):
        self.max_size = max_size
        self.default_ttl = default_ttl
        self._lock = threading.RLock()
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self.cache_hits = 0
        self.cache_misses = 0

    def _evict(self) -> None:
        while len(self._store) > self.max_size:
            self._store.popitem(last=False)

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            item = self._store.get(key)
            if not item:
                self.cache_misses += 1
                return None
            expiry, value = item
            if expiry < time.time():
                del self._store[key]
                self.cache_misses += 1
                return None
            self._store.move_to_end(key)
            self.cache_hits += 1
            return value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        ttl = ttl or self.default_ttl
        with self._lock:
            self._store[key] = (time.time() + ttl, value)
            self._store.move_to_end(key)
            self._evict()

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self.cache_hits = 0
            self.cache_misses = 0


def make_cache_key(*parts: str) -> str:
    raw = "||".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
