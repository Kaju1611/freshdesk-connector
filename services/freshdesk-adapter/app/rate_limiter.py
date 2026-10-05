"""Per-tenant rate limiting, shared across adapter replicas via Redis.

Two layers cooperate:
  * proactive: a per-tenant requests-per-minute budget, so we rarely hit Freshdesk's limit;
  * reactive : when Freshdesk answers 429, `block()` pauses that tenant for Retry-After
               seconds *for every replica*, so one replica's 429 protects the others.

`reserve()` returns how many seconds the caller must wait (0 = send now).
"""
from __future__ import annotations
import time
from typing import Callable, Protocol

WINDOW = 60  # Freshdesk limits are per minute


class Limiter(Protocol):
    async def reserve(self, tenant: str) -> float: ...
    async def block(self, tenant: str, seconds: float) -> None: ...


class InMemoryLimiter:
    def __init__(self, rpm: int, now: Callable[[], float] = time.time):
        self.rpm, self.now = rpm, now
        self._win: dict[str, tuple[int, int]] = {}
        self._blocked: dict[str, float] = {}

    async def reserve(self, tenant: str) -> float:
        n = self.now()
        if self._blocked.get(tenant, 0) > n:
            return self._blocked[tenant] - n
        w = int(n // WINDOW)
        cw, c = self._win.get(tenant, (w, 0))
        c = 0 if cw != w else c
        c += 1
        self._win[tenant] = (w, c)
        return (WINDOW - (n % WINDOW)) + 0.05 if c > self.rpm else 0.0

    async def block(self, tenant: str, seconds: float) -> None:
        self._blocked[tenant] = max(self._blocked.get(tenant, 0), self.now() + seconds)


class RedisLimiter:
    """Fixed-window counter (INCR is atomic) + a TTL'd 'blocked' key."""

    def __init__(self, redis, rpm: int, now: Callable[[], float] = time.time):
        self.r, self.rpm, self.now = redis, rpm, now

    async def reserve(self, tenant: str) -> float:
        pttl = await self.r.pttl(f"fd:block:{tenant}")
        if pttl and pttl > 0:
            return pttl / 1000
        n = self.now()
        key = f"fd:rl:{tenant}:{int(n // WINDOW)}"
        count = await self.r.incr(key)
        if count == 1:
            await self.r.expire(key, WINDOW * 2)
        return (WINDOW - (n % WINDOW)) + 0.05 if count > self.rpm else 0.0

    async def block(self, tenant: str, seconds: float) -> None:
        key = f"fd:block:{tenant}"
        new_ms = int(seconds * 1000)
        cur = await self.r.pttl(key)
        if not cur or cur < new_ms:           # never shorten an existing block
            await self.r.set(key, 1, px=max(new_ms, 1))
