"""In-process metrics counters.

Lightweight counters exposed via /status (and any future health endpoint).
Counters live in memory and reset on restart; the Store's ``metrics`` table
provides persistence when needed.
"""

from __future__ import annotations

import time
from collections import defaultdict


class Metrics:
    """Named integer counters with an optional last-updated timestamp."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._updated: dict[str, float] = {}
        self.started_at = time.time()

    def inc(self, name: str, amount: int = 1) -> None:
        self._counters[name] += amount
        self._updated[name] = time.time()

    def get(self, name: str) -> int:
        return self._counters.get(name, 0)

    def snapshot(self) -> dict[str, int]:
        return dict(self._counters)

    def uptime_seconds(self) -> float:
        return time.time() - self.started_at

    def reset(self) -> None:
        self._counters.clear()
        self._updated.clear()
        self.started_at = time.time()
