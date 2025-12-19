import time
from collections import deque
from typing import Deque, Dict

class RateLimiter:
    def __init__(self, max_per_minute: int = 60):
        self.max = max_per_minute
        self.window = 60
        self.buckets: Dict[str, Deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        bucket = self.buckets.setdefault(key, deque())
        # drop old
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        if len(bucket) >= self.max:
            return False
        bucket.append(now)
        return True

