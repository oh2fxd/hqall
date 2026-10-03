"""Tiny in-process TTL cache with stale fallback."""
from __future__ import annotations

import time
from typing import Any


class TTLCache:
    def __init__(self, max_entries: int = 256) -> None:
        self._d: dict[str, tuple[float, Any]] = {}
        self._max = max_entries

    def _prune_expired(self) -> None:
        now = time.time()
        for key in [key for key, (exp, _) in self._d.items() if exp <= now]:
            del self._d[key]

    def get(self, key: str) -> Any | None:
        e = self._d.get(key)
        if e is None:
            return None
        exp, val = e
        if exp <= time.time():
            del self._d[key]
            return None
        return val

    def put(self, key: str, value: Any, ttl: float = 60.0) -> None:
        self._prune_expired()
        if len(self._d) >= self._max:
            oldest = min(self._d, key=lambda k: self._d[k][0])
            del self._d[oldest]
        self._d[key] = (time.time() + ttl, value)

    def clear(self) -> None:
        self._d.clear()