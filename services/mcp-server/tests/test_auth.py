import pytest
from app.auth import hash_token, parse_tenant_tokens
from .conftest import TOK_A, TOK_B


def test_missing_and_wrong_token_rejected(rpc):
    assert rpc.post("tools/list", token=None).status_code == 401
    r = rpc.post("tools/list", token="nope")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")
    assert rpc.post("tools/list", token=TOK_A).status_code == 200


def test_healthz_is_public(rpc):
    assert rpc.tc.get("/healthz").json() == {"status": "ok"}


def test_tenant_comes_from_token_and_adapter_gets_service_token(rpc, adapter):
    rpc.call("get_ticket", {"ticket_id": 1}, token=TOK_A)
    rpc.call("get_ticket", {"ticket_id": 1}, token=TOK_B)
    assert [c.headers["x-tenant-id"] for c in adapter.calls] == ["tenant_a", "tenant_b"]
    assert all(c.headers["x-adapter-token"] == "adp-secret" for c in adapter.calls)


def test_no_tool_accepts_a_tenant_argument(rpc):
    tools = rpc.post("tools/list").json()["result"]["tools"]
    assert {t["name"] for t in tools} == {"list_tickets", "get_ticket", "search_tickets"}
    for t in tools:
        assert not [p for p in t["inputSchema"]["properties"] if "tenant" in p.lower()]
        assert t["annotations"]["readOnlyHint"] is True and t["annotations"]["destructiveHint"] is False


def test_concurrent_requests_never_cross_tenants(rpc):
    """Adapter echoes the tenant it saw as the ticket subject; each caller must get its own."""
    from concurrent.futures import ThreadPoolExecutor
    jobs = [(TOK_A, "tenant_a"), (TOK_B, "tenant_b")] * 20

    def one(job):
        tok, want = job
        is_err, body = rpc.call("get_ticket", {"ticket_id": 7}, token=tok)
        return not is_err and body["subject"] == want

    with ThreadPoolExecutor(8) as ex:
        assert all(ex.map(one, jobs))


def test_token_config_parsing():
    h = hash_token("x")
    assert parse_tenant_tokens(f"{h}:acme") == {h: "acme"}
    for bad in ["", "abc:acme", f"{h}", f"{h}:bad tenant", f"{h}:"]:
        with pytest.raises(ValueError):
            parse_tenant_tokens(bad)
