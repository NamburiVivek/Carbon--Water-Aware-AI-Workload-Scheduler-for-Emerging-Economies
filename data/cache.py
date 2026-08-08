"""
data/cache.py
Lightweight in-memory TTL cache for environmental data.
Slots Redis as a drop-in replacement when configured.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional


class MemoryCache:
    """Simple thread-safe TTL cache backed by a dict."""

    def __init__(self, default_ttl: int = 900) -> None:
        self._store: dict[str, tuple[Any, float]] = {}
        self._lock = threading.RLock()
        self._default_ttl = default_ttl

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        ttl = ttl if ttl is not None else self._default_ttl
        expires_at = time.monotonic() + ttl
        with self._lock:
            self._store[key] = (value, expires_at)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


def build_cache(backend: str = "memory", redis_url: str = "", ttl: int = 900):
    """Factory that returns the appropriate cache implementation."""
    if backend == "redis":
        try:
            import redis  # type: ignore

            r = redis.from_url(redis_url, decode_responses=False)

            class RedisCache:
                def get(self, key: str) -> Optional[Any]:
                    import pickle
                    raw = r.get(key)
                    return pickle.loads(raw) if raw else None  # noqa: S301

                def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
                    import pickle
                    r.setex(key, ttl or 900, pickle.dumps(value))

                def delete(self, key: str) -> None:
                    r.delete(key)

                def clear(self) -> None:
                    r.flushdb()

            return RedisCache()
        except ImportError:
            import warnings
            warnings.warn(
                "redis package not installed; falling back to memory cache.",
                stacklevel=2,
            )
    return MemoryCache(default_ttl=ttl)
