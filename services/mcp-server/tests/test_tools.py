import httpx
from .conftest import err, ticket


def q(adapter, i=-1):
    return adapter.calls[i].url.params


def test_list_tickets_params_and_shape(rpc, adapter):
    bad, body = rpc.call("list_tickets", {"page": 2, "per_page": 10, "filter": "watching",
                                          "order_by": "updated_at", "order_type": "desc"})
    assert not bad
    p = q(adapter)
    assert (p["page"], p["per_page"], p["filter"], p["order_by"]) == ("2", "10", "watching", "updated_at")
    assert "updated_since" not in p
    assert body["has_more"] is True and body["next_page"] == 2
    assert any("30 days" in n for n in body["notes"])          # honest coverage note
    assert "description_text" not in body["tickets"][0]        # summaries stay compact


def test_list_with_updated_since_drops_coverage_note(rpc):
    _, body = rpc.call("list_tickets", {"updated_since": "2025-01-01"})
    assert body["notes"] == []


def test_get_ticket_include_flags_and_clipping(rpc, adapter):
    adapter.script = [httpx.Response(200, json=ticket(5, description_text="x" * 200, requester={"id": 9, "email": "a@b.c"},
                      conversations=[{"id": 1, "body_text": "y" * 100, "incoming": True, "created_at": None},
                                     {"id": 2, "body_text": "ok", "incoming": False, "created_at": None}]))]
    bad, t = rpc.call("get_ticket", {"ticket_id": 5, "include_conversations": True, "include_requester": True})
    assert not bad
    assert sorted(adapter.calls[0].url.params.get_list("include")) == ["conversations", "requester"]
    assert t["description_text"].endswith("[truncated]") and len(t["description_text"]) < 80
    assert t["truncated"] is True
    assert [c["from"] for c in t["conversations"]] == ["customer", "agent"]
    assert "customer-provided" in t["notice"]                   # injection warning travels with the data
    assert t["requester"]["email"] == "a@b.c"


def test_get_ticket_without_includes_sends_none(rpc, adapter):
    rpc.call("get_ticket", {"ticket_id": 5})
    assert "include" not in adapter.calls[0].url.params


def test_search_maps_names_to_codes_and_notes_cap(rpc, adapter):
    adapter.script = [httpx.Response(200, json={"items": [ticket(1)], "total": 450, "page": 1, "has_more": True})]
    bad, body = rpc.call("search_tickets", {"status": ["open", "pending"], "priority": ["urgent"],
                                            "ticket_type": "Incident", "created_after": "2026-01-01"})
    assert not bad
    p = q(adapter)
    assert p.get_list("status") == ["2", "3"] and p.get_list("priority") == ["4"]
    assert p["type"] == "Incident" and p["created_after"] == "2026-01-01"
    assert body["next_page"] == 2 and any("300" in n for n in body["notes"])


def test_schema_validation_rejects_bad_args_without_calling_adapter(rpc, adapter):
    for name, args in [("list_tickets", {"per_page": 500}), ("search_tickets", {"status": ["bogus"]}),
                       ("get_ticket", {"ticket_id": 0}), ("get_ticket", {})]:
        bad, _ = rpc.call(name, args)
        assert bad
    assert adapter.calls == []


# -------- error translation: every message tells the agent what to do next --------
def run_err(rpc, adapter, response, name="get_ticket", args=None):
    adapter.script = [response]
    bad, msg = rpc.call(name, args or {"ticket_id": 42})
    assert bad
    return msg


def test_rate_limited_message_has_wait_time(rpc, adapter):
    m = run_err(rpc, adapter, err(429, "rate_limited", retryable=True, retry_after_seconds=30))
    assert "rate_limited" in m and "30 seconds" in m


def test_not_found_mentions_ticket_and_says_dont_retry(rpc, adapter):
    m = run_err(rpc, adapter, err(404, "not_found"))
    assert "Ticket 42" in m and "Do not retry" in m


def test_config_and_auth_errors_say_retry_is_pointless(rpc, adapter):
    assert "administrator" in run_err(rpc, adapter, err(404, "credentials_not_found"))
    m = run_err(rpc, adapter, err(502, "upstream_auth_error"))
    assert "Retrying will not help" in m


def test_validation_error_lists_field_problems(rpc, adapter):
    m = run_err(rpc, adapter, err(422, "validation_error", errors=[{"loc": ["query", "created_after"], "msg": "bad date"}]),
                "search_tickets", {"tag": "x"})
    assert "invalid_request" in m and "bad date" in m


def test_transient_failures_suggest_one_retry(rpc, adapter):
    assert "Retry once" in run_err(rpc, adapter, err(502, "upstream_error", retryable=True))
    assert "Retry once" in run_err(rpc, adapter, httpx.ConnectError("down"))
    assert "Retry once" in run_err(rpc, adapter, httpx.ReadTimeout("slow"))
    assert "Retry once" in run_err(rpc, adapter, httpx.Response(500, text="<html>boom</html>"))


def test_no_internal_secrets_in_any_output(rpc, adapter):
    texts = []
    for resp in [None, err(502, "upstream_auth_error"), err(429, "rate_limited", retry_after_seconds=5)]:
        if resp:
            adapter.script = [resp]
        _, out = rpc.call("get_ticket", {"ticket_id": 1})
        texts.append(str(out))
    assert not any("adp-secret" in t or "tok-tenant" in t for t in texts)
