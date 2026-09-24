"""Short-lived TTL cache for search results.

Keeps the results of recent /search queries so repeated or overlapping
queries do not re-crawl catalogue pages. Entries expire after *ttl* seconds.
"""

from __future__ import annotations

import time
from typing import Any


class SearchCache:
    """A tiny key-value cache with a per-entry TTL."""

    def __init__(self, ttl: float = 60.0, max_entries: int = 256) -> None:
        self.ttl = ttl
        self.max_entries = max_entries
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        """Return the cached value, or None if absent / expired."""
        entry = self._data.get(key)
        if entry is None:
            return None
        ts, value = entry
        if time.monotonic() - ts > self.ttl:
            del self._data[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        """Store *value* under *key*, evicting the oldest entry if full."""
        if len(self._data) >= self.max_entries:
            oldest = min(self._data, key=lambda k: self._data[k][0])
            del self._data[oldest]
        self._data[key] = (time.monotonic(), value)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)
