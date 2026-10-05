import asyncio
import httpx
import pytest

from .conftest import HDR, raw_ticket

R429 = lambda s: httpx.Response(429, headers={"retry-after": str(s)}, json={})


def test_429_waits_retry_after_then_succeeds(make_client, world, clock):
    world.script = [R429(2)]
    r = make_client().get("/v1/tickets/1", headers=HDR)
    assert r.status_code == 200
    assert len(world.fd_calls) == 2
    assert clock.sleeps == [2.0]               # honored Retry-After exactly


def test_429_with_long_retry_after_sheds_load(make_client, world, clock):
    world.script = [R429(60)]
    r = make_client().get("/v1/tickets/1", headers=HDR)
    b = r.json()["error"]
    assert r.status_code == 429 and b["code"] == "rate_limited" and b["retryable"] is True
    assert b["details"]["retry_after_seconds"] == 60
    assert r.headers["retry-after"] == "60"
    assert len(world.fd_calls) == 1 and clock.sleeps == []   # no sleeping, no hammering


def test_block_is_remembered_across_requests(make_client, world):
    c = make_client(cache_ttl_seconds=0)
    world.script = [R429(60)]
    assert c.get("/v1/tickets/1", headers=HDR).status_code == 429
    n = len(world.fd_calls)
    assert c.get("/v1/tickets/2", headers=HDR).status_code == 429
    assert len(world.fd_calls) == n            # second request never reached Freshdesk


def test_block_is_per_tenant(make_client, world):
    c = make_client(cache_ttl_seconds=0)
    world.tenants["t2"] = ("other", "key-BBBB-2222")
    world.script = [R429(60)]
    assert c.get("/v1/tickets/1", headers=HDR).status_code == 429
    assert c.get("/v1/tickets/1", headers={**HDR, "X-Tenant-Id": "t2"}).status_code == 200


def test_repeated_429_gives_up_after_max_retries(make_client, world):
    world.script = [R429(1) for _ in range(10)]
    r = make_client(max_retries=2).get("/v1/tickets/1", headers=HDR)
    assert r.status_code == 429 and len(world.fd_calls) == 3


def test_missing_retry_after_defaults_to_60_and_sheds(make_client, world):
    world.script = [httpx.Response(429, json={})]
    r = make_client().get("/v1/tickets/1", headers=HDR)
    assert r.status_code == 429 and r.json()["error"]["details"]["retry_after_seconds"] == 60


def test_proactive_budget_waits_for_next_window(make_client, world, clock):
    clock.t = 6059.0                           # 1s before the window rolls over
    c = make_client(tenant_rpm=2, cache_ttl_seconds=0)
    for i in (1, 2, 3):
        assert c.get(f"/v1/tickets/{i}", headers=HDR).status_code == 200
    assert clock.sleeps == [1.05]              # third call waited for the next minute
    assert len(world.fd_calls) == 3


def test_proactive_budget_sheds_when_wait_too_long(make_client, world, clock):
    clock.t = 6000.0                           # a full minute away from reset
    c = make_client(tenant_rpm=2, cache_ttl_seconds=0)
    assert [c.get(f"/v1/tickets/{i}", headers=HDR).status_code for i in (1, 2)] == [200, 200]
    r = c.get("/v1/tickets/3", headers=HDR)
    assert r.status_code == 429 and len(world.fd_calls) == 2


# ---------- 5xx / network retries ----------
def test_5xx_retried_with_exponential_backoff(make_client, world, clock):
    world.script = [httpx.Response(503), httpx.Response(502), httpx.Response(200, json=raw_ticket(1))]
    assert make_client().get("/v1/tickets/1", headers=HDR).status_code == 200
    assert clock.sleeps == [0.5, 1.0]


def test_5xx_exhausts_retries(make_client, world):
    world.script = [httpx.Response(503) for _ in range(10)]
    r = make_client(max_retries=3).get("/v1/tickets/1", headers=HDR)
    assert r.status_code == 502 and r.json()["error"]["retryable"] is True
    assert len(world.fd_calls) == 4


def test_network_error_retried(make_client, world, clock):
    def boom(req):
        raise httpx.ConnectError("down")
    world.script = [boom, httpx.Response(200, json=raw_ticket(1))]
    assert make_client().get("/v1/tickets/1", headers=HDR).status_code == 200
    assert clock.sleeps == [0.5]


# ---------- shared (Redis-backed) limiter ----------
fakeredis = pytest.importorskip("fakeredis")


def test_redis_limiter_shares_state_between_replicas():
    from app.rate_limiter import RedisLimiter
    server = fakeredis.FakeServer()
    a = RedisLimiter(fakeredis.FakeAsyncRedis(server=server), rpm=2, now=lambda: 6000.0)
    b = RedisLimiter(fakeredis.FakeAsyncRedis(server=server), rpm=2, now=lambda: 6000.0)

    async def go():
        assert await a.reserve("t") == 0
        assert await b.reserve("t") == 0          # replica B sees A's usage
        assert await a.reserve("t") > 0           # budget exhausted for everyone
        await a.block("u", 30)
        assert 29 < await b.reserve("u") <= 30    # A's 429 block protects B
        await b.block("u", 5)                     # must not shorten an existing block
        assert await a.reserve("u") > 25
    asyncio.run(go())


def test_redis_cache_roundtrip():
    from app.cache import RedisCache
    c = RedisCache(fakeredis.FakeAsyncRedis())

    async def go():
        assert await c.get("k") is None
        await c.set("k", {"a": 1}, 30)
        assert await c.get("k") == {"a": 1}
    asyncio.run(go())
