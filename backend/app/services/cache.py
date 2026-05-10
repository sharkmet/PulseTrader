"""Simple TTL-based in-memory cache. Thread-safe via a lock."""
from __future__ import annotations

import threading
import time
from typing import Any, TypeVar

T = TypeVar("T")


class _CacheEntry:
    __slots__ = ("expires_at", "value")

    def __init__(self, value: Any, ttl: int) -> None:
        self.value = value
        self.expires_at = time.monotonic() + ttl


class TTLCache:
    """Key-value cache with per-entry TTL (seconds)."""

    def __init__(self) -> None:
        self._store: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if time.monotonic() > entry.expires_at:
                del self._store[key]
                return None
            return entry.value

    def set(self, key: str, value: Any, ttl: int) -> None:
        with self._lock:
            self._store[key] = _CacheEntry(value, ttl)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def evict_expired(self) -> int:
        now = time.monotonic()
        with self._lock:
            expired = [k for k, e in self._store.items() if now > e.expires_at]
            for k in expired:
                del self._store[k]
        return len(expired)

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


# Singleton shared across the application.
cache = TTLCache()
