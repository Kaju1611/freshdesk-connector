import base64
import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

HDR = {"X-Adapter-Token": "adp", "X-Tenant-Id": "t1"}


def raw_ticket(i=1, **kw):
    return {"id": i, "subject": "Refund", "status": 2, "priority": 3, "requester_id": 9,
            "responder_id": None, "group_id": None, "type": "Question", "tags": ["refund"],
            "description_text": "hi", "created_at": "2026-09-01T10:00:00Z",
            "updated_at": "2026-09-02T10:00:00Z", "due_by": None, **kw}


def basic(key):
    return "Basic " + base64.b64encode(f"{key}:X".encode()).decode()


class Clock:
    """Fake time: sleeping advances the clock, so waits are observable and instant."""
    def __init__(self, t=6000.0):
        self.t, self.sleeps = t, []

    def now(self):
        return self.t

    async def sleep(self, d):
        self.sleeps.append(round(d, 3))
        self.t += d


class World:
    """Fake credential-service + fake Freshdesk."""
    def __init__(self):
        self.tenants = {"t1": ("acme", "key-AAAA-1111")}
        self.cred_calls = 0
        self.fd_calls: list[httpx.Request] = []
        self.script: list = []          # responses / callables consumed in order, then default

    def default(self, req):
        p = req.url.path
        if p.endswith("/search/tickets"):
            return httpx.Response(200, json={"total": 1, "results": [raw_ticket()]})
        if p.endswith("/api/v2/tickets"):
            return httpx.Response(200, json=[raw_ticket(1), raw_ticket(2)])
        return httpx.Response(200, json=raw_ticket(int(p.rsplit("/", 1)[1])))

    def handler(self, req: httpx.Request):
        if req.url.host == "cred.internal":
            self.cred_calls += 1
            tid = req.url.path.split("/")[4]
            if tid not in self.tenants:
                return httpx.Response(404, json={})
            d, k = self.tenants[tid]
            return httpx.Response(200, json={"tenant_id": tid, "domain": d, "api_key": k})
        self.fd_calls.append(req)
        if self.script:
            nxt = self.script.pop(0)
            return nxt(req) if callable(nxt) else nxt
        return self.default(req)


@pytest.fixture
def world():
    return World()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def make_client(world, clock):
    def _make(**overrides):
        s = Settings(credential_service_url="http://cred.internal", internal_token="int",
                     adapter_token="adp", **overrides)
        http = httpx.AsyncClient(transport=httpx.MockTransport(world.handler))
        app = create_app(s, http_client=http, sleep=clock.sleep, rand=lambda: 1.0)
        # limiter/cache time must follow the fake clock too
        from app.rate_limiter import InMemoryLimiter
        app.state.client.limiter = InMemoryLimiter(s.tenant_rpm, now=clock.now)
        return TestClient(app)
    return _make
