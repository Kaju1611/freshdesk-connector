"""Short-lived response cache. Keys always include the tenant id (no cross-tenant leakage)."""
from __future__ import annotations
import json, time
from typing import Any, Callable, Protocol


class Cache(Protocol):
    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any, ttl: int) -> None: ...


class InMemoryCache:
    def __init__(self, max_items: int = 2000, now: Callable[[], float] = time.monotonic):
        self._d: dict[str, tuple[float, Any]] = {}
        self.max_items, self.now = max_items, now

    async def get(self, key):
        hit = self._d.get(key)
        if not hit:
            return None
        if hit[0] < self.now():
            self._d.pop(key, None)
            return None
        return hit[1]

    async def set(self, key, value, ttl):
        if ttl <= 0:
            return
        if len(self._d) >= self.max_items:
            self._d.pop(next(iter(self._d)))   # evict oldest insertion
        self._d[key] = (self.now() + ttl, value)


class RedisCache:
    def __init__(self, redis):
        self.r = redis

    async def get(self, key):
        v = await self.r.get(key)
        return json.loads(v) if v else None

    async def set(self, key, value, ttl):
        if ttl > 0:
            await self.r.set(key, json.dumps(value), ex=ttl)
