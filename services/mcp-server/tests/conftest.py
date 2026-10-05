import asyncio
import json
import random

import httpx
import pytest
from starlette.testclient import TestClient

from app.auth import hash_token
from app.config import Settings
from app.main import create_app

TOK_A, TOK_B = "tok-tenant-a-secret", "tok-tenant-b-secret"
H = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def ticket(i=1, **kw):
    return {"id": i, "subject": "Refund", "status_name": "open", "priority_name": "high", "type": "Question",
            "tags": ["refund"], "requester_id": 9, "responder_id": None, "group_id": None,
            "description_text": "hello", "created_at": "2026-09-01T10:00:00Z",
            "updated_at": "2026-09-02T10:00:00Z", "due_by": None, **kw}


class FakeAdapter:
    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.script: list = []

    async def handler(self, req: httpx.Request):
        self.calls.append(req)
        await asyncio.sleep(random.random() * 0.01)
        if self.script:
            nxt = self.script.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt
        p = req.url.path
        t = req.headers.get("x-tenant-id")
        if p == "/v1/tickets/search":
            return httpx.Response(200, json={"items": [ticket(1, subject=t)], "total": 1, "page": 1, "has_more": False})
        if p == "/v1/tickets":
            return httpx.Response(200, json={"items": [ticket(1, subject=t)], "page": 1, "per_page": 20, "has_more": True})
        return httpx.Response(200, json=ticket(int(p.rsplit("/", 1)[1]), subject=t))


def err(status, code, message="m", retryable=False, **details):
    return httpx.Response(status, json={"error": {"code": code, "message": message,
                                                  "retryable": retryable, "details": details}})


class Rpc:
    def __init__(self, tc: TestClient):
        self.tc, self._id = tc, 0

    def post(self, method, params=None, token=TOK_A):
        self._id += 1
        h = dict(H)
        if token:
            h["Authorization"] = f"Bearer {token}"
        return self.tc.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": self._id, "method": method,
                                                      "params": params or {}})

    def call(self, name, args=None, token=TOK_A):
        """-> (is_error, parsed_json_or_text)"""
        r = self.post("tools/call", {"name": name, "arguments": args or {}}, token)
        assert r.status_code == 200, r.text
        res = r.json()["result"]
        text = res["content"][0]["text"]
        if res["isError"]:
            return True, text
        return False, json.loads(text)


@pytest.fixture
def adapter():
    return FakeAdapter()


@pytest.fixture
def rpc(adapter):
    s = Settings(adapter_url="http://adapter.internal", adapter_token="adp-secret",
                 tenant_tokens=f"{hash_token(TOK_A)}:tenant_a,{hash_token(TOK_B)}:tenant_b",
                 max_text_chars=50, max_note_chars=20)
    http = httpx.AsyncClient(transport=httpx.MockTransport(adapter.handler))
    with TestClient(create_app(s, adapter_http=http)) as tc:
        yield Rpc(tc)
