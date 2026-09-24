"""Per-user command rate limiting.

A simple sliding-window limiter: each user gets a bucket of N requests per
window. When the bucket is full further requests are rejected until the
oldest entry falls out of the window.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    """Allow at most *max_calls* per *window* seconds for each key."""

    def __init__(self, max_calls: int = 10, window: float = 60.0) -> None:
        self.max_calls = max_calls
        self.window = window
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        """Return True if *key* may make one more call right now."""
        now = time.monotonic()
        bucket = self._hits[key]
        # Drop timestamps that have fallen out of the window.
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        if len(bucket) >= self.max_calls:
            return False
        bucket.append(now)
        return True

    def remaining(self, key: str) -> int:
        """How many calls *key* can still make in the current window."""
        now = time.monotonic()
        bucket = self._hits[key]
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        return max(0, self.max_calls - len(bucket))

    def retry_after(self, key: str) -> float:
        """Seconds until *key* gets one more call back (0 if already allowed)."""
        now = time.monotonic()
        bucket = self._hits[key]
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        if len(bucket) < self.max_calls:
            return 0.0
        return max(0.0, bucket[0] + self.window - now)

    def reset(self, key: str | None = None) -> None:
        """Clear one key, or all keys if *key* is None."""
        if key is None:
            self._hits.clear()
        else:
            self._hits.pop(key, None)
