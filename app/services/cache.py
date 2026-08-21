"""Optional Redis cache layer.

If REDIS_URL is set in the environment, this module returns a real Redis
client backed by hiredis. If REDIS_URL is not set (dev / default single-worker
deploy), the app uses a process-local TTL cache so hot list/dashboard paths
still benefit from caching without an external service. Accepted tradeoff:
process-local invalidation is not shared across multiple worker processes, so a
multi-worker deployment without Redis can serve stale reads for up to the TTL
window. Set REDIS_URL before scaling beyond one worker.

Usage
-----
    from app.services.cache import get_cache

    cache = get_cache()
    hit = cache.get(key)
    if hit is None:
        value = compute_expensive_thing()
        cache.set(key, value, ttl=60)

Cache keys
----------
Candidates list : "cands:{tenant}:{hash_of_params}"
Demands list    : "demands:{tenant}:{hash_of_params}"
Dashboard stats : "dash:{tenant}"

All keys are invalidated via ``cache.delete_pattern("prefix:*")`` on writes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Optional

log = logging.getLogger("dtc.cache")

_DEFAULT_TTL = 60  # seconds


class _NoOpCache:
    """Transparent stub — every get() returns None (cache miss)."""

    def get(self, key: str) -> Optional[str]:
        return None

    def set(self, key: str, value: Any, ttl: int = _DEFAULT_TTL) -> None:
        pass

    def delete(self, key: str) -> None:
        pass

    def delete_pattern(self, pattern: str) -> int:
        return 0

    @property
    def available(self) -> bool:
        return False


class _InMemoryCache:
    """Process-local TTL cache used when REDIS_URL is not configured.

    PERFORMANCE (2026-07-02): previously the no-Redis path fell back to the
    no-op stub, which meant every list endpoint recomputed its full query +
    context join on every request. This LRU+TTL cache gives single-worker
    deployments (the default: uvicorn --workers 1) the same hot-path caching
    the code was already written for — with zero external dependencies.

    Safety notes:
    - Keys already embed the tenant slug (see make_key call sites), so
      multi-tenant isolation is preserved.
    - Values are stored via JSON round-trip (same contract as Redis) so
      callers can never mutate a cached object in place.
    - Bounded to _MAX_ENTRIES with oldest-first eviction to cap memory.
    - NOT shared across workers. If you scale to --workers N>1, set
      REDIS_URL so invalidation propagates; with in-memory cache the worst
      case is a stale read for up to the TTL (60s), never cross-tenant data.
    """

    _MAX_ENTRIES = 512

    def __init__(self) -> None:
        import threading
        from collections import OrderedDict
        self._lock = threading.Lock()
        self._store: OrderedDict[str, tuple[float, str]] = OrderedDict()

    def get(self, key: str) -> Optional[Any]:
        import time
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            expires_at, raw = item
            if time.monotonic() >= expires_at:
                self._store.pop(key, None)
                return None
            self._store.move_to_end(key)  # LRU touch
        try:
            return json.loads(raw)
        except (ValueError, TypeError) as exc:
            # AUDIT 2026-07-31: this used to be a bare `except Exception: return
            # None`, which left the undecodable entry IN the store -- so every
            # subsequent read of that key paid the JSON parse cost, failed again,
            # and reported a cache miss until the TTL expired. Evict the poisoned
            # key so the next caller repopulates it, and say so in the log.
            log.warning("evicting undecodable cache entry key=%s: %s", key, exc)
            with self._lock:
                self._store.pop(key, None)
            return None

    def set(self, key: str, value: Any, ttl: int = _DEFAULT_TTL) -> None:
        import time
        try:
            raw = json.dumps(value, default=str)
        except Exception as exc:
            log.warning("memcache.set(%s) serialise failed: %s", key, exc)
            return
        with self._lock:
            self._store[key] = (time.monotonic() + ttl, raw)
            self._store.move_to_end(key)
            while len(self._store) > self._MAX_ENTRIES:
                self._store.popitem(last=False)  # evict oldest

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def delete_pattern(self, pattern: str) -> int:
        import fnmatch
        with self._lock:
            doomed = [k for k in self._store if fnmatch.fnmatch(k, pattern)]
            for k in doomed:
                del self._store[k]
            return len(doomed)

    @property
    def available(self) -> bool:
        return True


class _RedisCache:
    """Thin wrapper around redis.Redis with JSON serialisation."""

    def __init__(self, client):
        self._r = client

    def get(self, key: str) -> Optional[Any]:
        try:
            raw = self._r.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            log.warning("cache.get(%s) failed: %s", key, exc)
            return None

    def set(self, key: str, value: Any, ttl: int = _DEFAULT_TTL) -> None:
        try:
            self._r.setex(key, ttl, json.dumps(value, default=str))
        except Exception as exc:
            log.warning("cache.set(%s) failed: %s", key, exc)

    def delete(self, key: str) -> None:
        try:
            self._r.delete(key)
        except Exception as exc:
            log.warning("cache.delete(%s) failed: %s", key, exc)

    def delete_pattern(self, pattern: str) -> int:
        """Delete all keys matching a glob pattern. Returns count deleted."""
        try:
            keys = self._r.keys(pattern)
            if keys:
                return self._r.delete(*keys)
            return 0
        except Exception as exc:
            log.warning("cache.delete_pattern(%s) failed: %s", pattern, exc)
            return 0

    @property
    def available(self) -> bool:
        return True


_cache_instance: Optional[_NoOpCache | _InMemoryCache | _RedisCache] = None


def get_cache() -> _NoOpCache | _InMemoryCache | _RedisCache:
    """Return the singleton cache instance (Redis, in-memory TTL, or no-op stub)."""
    global _cache_instance
    if _cache_instance is not None:
        return _cache_instance

    redis_url = os.environ.get("REDIS_URL", "")
    if redis_url:
        try:
            import redis  # type: ignore[import-untyped]
            client = redis.from_url(redis_url, decode_responses=True)
            client.ping()  # fail fast if URL is wrong
            _cache_instance = _RedisCache(client)
            log.info("Redis cache connected: %s", redis_url.split("@")[-1])
        except Exception as exc:
            log.warning(
                "Redis connection failed (%s) — falling back to no-op cache", exc
            )
            _cache_instance = _NoOpCache()
    elif os.environ.get("TESTING") == "true" or os.environ.get("DISABLE_CACHE") == "true":
        # Tests must always exercise the real query path; the no-op stub
        # guarantees deterministic, cache-free behaviour.
        log.debug("cache disabled (TESTING/DISABLE_CACHE) — using no-op stub")
        _cache_instance = _NoOpCache()
    else:
        log.info("REDIS_URL not set — using in-process TTL cache (single-worker)")
        _cache_instance = _InMemoryCache()

    return _cache_instance


def make_key(prefix: str, tenant: str, **params) -> str:
    """Deterministic cache key from prefix, tenant, and query params."""
    stable = json.dumps(params, sort_keys=True, default=str)
    digest = hashlib.md5(stable.encode()).hexdigest()[:12]
    return f"{prefix}:{tenant}:{digest}"
