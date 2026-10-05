"""End-to-end: real MCP client -> mcp-server -> freshdesk-adapter -> credential-service -> stub Freshdesk."""
import asyncio
import json
import re
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from conftest import ADAPTER, ADMIN, INTERNAL, TENANTS

NOW = datetime.now(timezone.utc)
iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
ago = lambda days: NOW - timedelta(days=days)


def mk(i, subject, status=2, priority=2, tags=(), created=1, updated=None, **kw):
    return {"id": i, "subject": subject, "status": status, "priority": priority, "tags": list(tags),
            "type": "Question", "requester_id": 100 + i, "responder_id": None, "group_id": None,
            "description_text": f"body of {subject}", "due_by": None,
            "created_at": iso(ago(created)), "updated_at": iso(ago(updated if updated is not None else created)), **kw}


# ------------------------------------------------------------------ helpers
def call(stack, tenant, tool, args=None):
    """Call a tool through the real MCP client. -> (is_error, parsed_json | error_text)"""
    token = TENANTS[tenant][0]

    async def go():
        hc = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
        async with streamable_http_client(stack.mcp + "/mcp", http_client=hc) as streams:
            async with ClientSession(streams[0], streams[1]) as s:
                await s.initialize()
                return await s.call_tool(tool, args or {})
    res = asyncio.run(go())
    text = res.content[0].text
    return (True, text) if res.is_error else (False, json.loads(text))


def ids(body):
    return {t["id"] for t in body["tickets"]}


@pytest.fixture(scope="module", autouse=True)
def seeded(stack):
    big = "long " * 400                                        # 2000 chars -> must be clipped
    stack.seed("acme", "key-acme-0001", [
        mk(1, "ACME refund request", status=2, priority=4, tags=["refund"]),
        mk(2, "ACME shipping delay", status=3, priority=2, tags=["shipping"]),
        mk(3, "ACME wrong size", status=4, priority=1),
        mk(4, "ACME old chargeback", status=5, priority=2, tags=["refund"], created=90, updated=89),
        mk(5, "ACME thread", status=2, priority=1,
           requester={"id": 105, "name": "Pat", "email": "pat@example.com"},
           conversations=[
               {"id": 1, "body_text": big, "incoming": True, "private": False, "created_at": iso(ago(1))},
               {"id": 2, "body_text": "INTERNAL-ONLY-NOTE: customer is a fraud risk", "incoming": False,
                "private": True, "created_at": iso(ago(1))},
               {"id": 3, "body_text": "We have refunded you.", "incoming": False, "private": False,
                "created_at": iso(ago(0))}]),
    ])
    stack.seed("globex", "key-globex-0002", [mk(i, f"GLOBEX ticket {i}", status=2 + i % 2, created=1, updated=i % 28)
                                              for i in range(100, 145)])   # 45 tickets
    for dom, key in (("ratelimited", "key-rl-0003"), ("shedding", "key-shed-0004"),
                     ("rotating", "key-rot-0005"), ("flaky", "key-flaky-0006")):
        stack.seed(dom, key, [mk(1, f"{dom} ticket")])
    for ten, dom, key in (("acme_ws", "acme", "key-acme-0001"), ("globex_ws", "globex", "key-globex-0002"),
                          ("rl_ws", "ratelimited", "key-rl-0003"), ("shed_ws", "shedding", "key-shed-0004"),
                          ("rot_ws", "rotating", "key-rot-0005"), ("flaky_ws", "flaky", "key-flaky-0006")):
        r = stack.register(ten, dom, key)
        assert r.status_code == 200, r.text


# ------------------------------------------------------------------ onboarding & auth
def test_onboarding_rejects_a_bad_key_and_stores_nothing(stack):
    r = stack.register("bad_ws", "acme", "definitely-wrong-key")
    assert r.status_code == 401 and r.json()["error"]["code"] == "auth_error"
    assert httpx.get(f"{stack.cred}/v1/tenants/bad_ws", headers={"X-Admin-Token": ADMIN}).status_code == 404


def test_every_service_rejects_unauthenticated_callers(stack):
    assert httpx.post(stack.mcp + "/mcp", json={}).status_code == 401
    assert httpx.post(stack.mcp + "/mcp", json={}, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert httpx.get(stack.adapter + "/v1/tickets", headers={"X-Tenant-Id": "acme_ws"}).status_code == 401
    # the adapter token must not unlock credential reads, and vice versa
    assert httpx.get(f"{stack.cred}/internal/v1/tenants/acme_ws/credentials",
                     headers={"X-Internal-Token": ADAPTER}).status_code == 401
    assert httpx.get(f"{stack.cred}/internal/v1/tenants/acme_ws/credentials",
                     headers={"X-Admin-Token": ADMIN}).status_code == 401


def test_unregistered_workspace_gets_actionable_error(stack):
    bad, msg = call(stack, "ghost_ws", "list_tickets")
    assert bad and "not_configured" in msg and "administrator" in msg


def test_tools_are_discoverable_and_read_only(stack):
    async def go():
        hc = create_mcp_http_client(headers={"Authorization": "Bearer tok-acme"})
        async with streamable_http_client(stack.mcp + "/mcp", http_client=hc) as streams:
            async with ClientSession(streams[0], streams[1]) as s:
                await s.initialize()
                return (await s.list_tools()).tools
    tools = asyncio.run(go())
    assert {t.name for t in tools} == {"list_tickets", "get_ticket", "search_tickets"}
    assert all(t.annotations.read_only_hint is True for t in tools)


# ------------------------------------------------------------------ list / get / search
def test_list_defaults_to_last_30_days_and_says_so(stack):
    bad, body = call(stack, "acme_ws", "list_tickets")
    assert not bad and ids(body) == {1, 2, 3, 5}                 # 90-day-old ticket excluded
    assert any("30 days" in n for n in body["notes"])
    since = (NOW - timedelta(days=100)).strftime("%Y-%m-%d")
    bad, body = call(stack, "acme_ws", "list_tickets", {"updated_since": since})
    assert not bad and 4 in ids(body) and body["notes"] == []


def test_pagination_walks_all_pages(stack):
    seen, page = [], 1
    while page:
        bad, body = call(stack, "globex_ws", "list_tickets", {"page": page, "per_page": 20,
                                                              "updated_since": "2020-01-01"})
        assert not bad
        seen += [t["id"] for t in body["tickets"]]
        assert body["has_more"] == (page < 3)
        page = body["next_page"]
    assert len(seen) == 45 and len(set(seen)) == 45


def test_get_ticket_hides_private_notes_and_clips_text(stack):
    bad, t = call(stack, "acme_ws", "get_ticket", {"ticket_id": 5, "include_conversations": True,
                                                   "include_requester": True})
    assert not bad
    assert "INTERNAL-ONLY-NOTE" not in json.dumps(t)             # private note never reaches the agent
    assert [c["from"] for c in t["conversations"]] == ["customer", "agent"]
    assert t["conversations"][0]["body_text"].endswith("[truncated]") and t["truncated"] is True
    assert t["requester"]["email"] == "pat@example.com"
    assert "customer-provided" in t["notice"]


def test_get_unknown_ticket(stack):
    bad, msg = call(stack, "acme_ws", "get_ticket", {"ticket_id": 9999})
    assert bad and "Ticket 9999" in msg and "Do not retry" in msg


@pytest.mark.parametrize("args,expected", [
    ({"status": ["open"]}, {1, 5}),
    ({"status": ["open", "pending"]}, {1, 2, 5}),
    ({"tag": "refund"}, {1, 4}),
    ({"tag": "refund", "created_after": (NOW - timedelta(days=30)).strftime("%Y-%m-%d")}, {1}),
    ({"priority": ["urgent"]}, {1}),
    ({"query": "tag:'shipping'"}, {2}),
    ({"status": ["closed"], "updated_before": (NOW - timedelta(days=10)).strftime("%Y-%m-%d")}, {4}),
])
def test_search_filters_end_to_end(stack, args, expected):
    bad, body = call(stack, "acme_ws", "search_tickets", args)
    assert not bad, body
    assert ids(body) == expected and body["total"] == len(expected)


def test_search_with_invalid_raw_query_gives_fixable_error(stack):
    bad, msg = call(stack, "acme_ws", "search_tickets", {"query": "nonsense"})
    assert bad and "invalid_request" in msg


def test_search_requires_a_filter(stack):
    bad, msg = call(stack, "acme_ws", "search_tickets", {})
    assert bad and "invalid_request" in msg


# ------------------------------------------------------------------ isolation
def test_tenants_only_see_their_own_data_and_keys(stack):
    _, a = call(stack, "acme_ws", "list_tickets")
    _, g = call(stack, "globex_ws", "list_tickets", {"per_page": 5})
    assert all(t["subject"].startswith("ACME") for t in a["tickets"])
    assert all(t["subject"].startswith("GLOBEX") for t in g["tickets"])
    for dom, key in (("acme", "key-acme-0001"), ("globex", "key-globex-0002")):
        log = stack.stub_log(dom)
        assert {e["key"] for e in log if e["status"] == 200} == {key}      # every success used its own key
        assert all(e["status"] == 401 for e in log if e["key"] != key)     # anything else was refused


# ------------------------------------------------------------------ resilience
def test_short_429_is_waited_out_transparently(stack):
    before = len(stack.stub_log("ratelimited"))
    stack.fail("ratelimited", {"status": 429, "retry_after": 1})
    t0 = time.time()
    bad, body = call(stack, "rl_ws", "list_tickets")
    elapsed = time.time() - t0
    assert not bad and 1 in ids(body)
    assert [e["status"] for e in stack.stub_log("ratelimited")[before:]] == [429, 200]
    assert 0.9 <= elapsed < 6                                   # honoured Retry-After, then succeeded


def test_long_429_sheds_load_and_blocks_only_that_tenant(stack):
    before = len(stack.stub_log("shedding"))
    stack.fail("shedding", {"status": 429, "retry_after": 60})
    t0 = time.time()
    bad, msg = call(stack, "shed_ws", "list_tickets")
    assert bad and "rate_limited" in msg and re.search(r"(59|60) seconds", msg)
    assert time.time() - t0 < 5                                 # did not hold the agent's call open
    assert [e["status"] for e in stack.stub_log("shedding")[before:]] == [429]

    bad, msg = call(stack, "shed_ws", "list_tickets")           # remembered block: Freshdesk not hit again
    assert bad and "rate_limited" in msg
    assert len(stack.stub_log("shedding")) == before + 1

    assert not call(stack, "acme_ws", "list_tickets")[0]        # other tenants unaffected


def test_5xx_is_retried_with_backoff(stack):
    before = len(stack.stub_log("flaky"))
    stack.fail("flaky", {"status": 503}, {"status": 503})
    bad, body = call(stack, "flaky_ws", "list_tickets")
    assert not bad
    assert [e["status"] for e in stack.stub_log("flaky")[before:]] == [503, 503, 200]


def test_key_rotation_recovers_after_reregistration(stack):
    assert not call(stack, "rot_ws", "list_tickets")[0]
    httpx.post(f"{stack.stub}/__admin/domains/rotating/key", json={"key": "key-rot-0007"})   # customer rotates key in Freshdesk
    bad, msg = call(stack, "rot_ws", "list_tickets")             # we still hold the old one
    assert bad and "auth_failed" in msg and "administrator" in msg
    assert stack.register("rot_ws", "rotating", "key-rot-0007").status_code == 200   # admin re-registers
    before = len(stack.stub_log("rotating"))
    assert not call(stack, "rot_ws", "list_tickets")[0]          # adapter's cached old key -> 401 -> refetch -> ok
    assert [(e["status"], e["key"]) for e in stack.stub_log("rotating")[before:]] == \
        [(401, "key-rot-0005"), (200, "key-rot-0007")]


# ------------------------------------------------------------------ hygiene (keep last)
def test_zz_no_secret_ever_appears_in_service_logs(stack):
    secrets = ["key-acme-0001", "key-globex-0002", "key-rl-0003", "key-shed-0004", "key-rot-0005",
               "key-rot-0007", "key-flaky-0006", ADMIN, INTERNAL, ADAPTER] + [t for t, _ in TENANTS.values()]
    for name in ("cred", "adapter", "mcp"):
        text = stack.logs[name].read_text()
        leaked = [s for s in secrets if s in text]
        assert not leaked, f"{name} log leaked {leaked}"
    assert "freshdesk call" in stack.logs["adapter"].read_text()     # guard against a vacuous pass
