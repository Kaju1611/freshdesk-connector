import httpx
import pytest

from .conftest import HDR, basic, raw_ticket


def test_get_ticket_maps_fields_and_uses_basic_auth(make_client, world):
    c = make_client()
    r = c.get("/v1/tickets/42", headers=HDR)
    assert r.status_code == 200
    b = r.json()
    assert (b["id"], b["status_name"], b["priority_name"]) == (42, "open", "high")
    req = world.fd_calls[0]
    assert req.url.host == "acme.freshdesk.com"
    assert req.headers["authorization"] == basic("key-AAAA-1111")


def test_list_has_more_from_link_header(make_client, world):
    c = make_client()
    world.script = [httpx.Response(200, json=[raw_ticket(1)],
                                   headers={"link": '<https://acme.freshdesk.com/api/v2/tickets?page=2>; rel="next"'})]
    r = c.get("/v1/tickets?page=1&per_page=1&order_by=updated_at&order_type=desc&filter=watching", headers=HDR).json()
    assert r["has_more"] is True and len(r["items"]) == 1
    q = dict(world.fd_calls[0].url.params)
    assert q["per_page"] == "1" and q["order_by"] == "updated_at" and q["filter"] == "watching"
    # last page: no link header and fewer than per_page rows -> no more
    world.script = [httpx.Response(200, json=[raw_ticket(1)])]
    assert c.get("/v1/tickets?page=2&per_page=30", headers=HDR).json()["has_more"] is False


def test_search_builds_query_and_pagination(make_client, world):
    c = make_client()
    world.script = [httpx.Response(200, json={"total": 95, "results": [raw_ticket(1)]})]
    r = c.get("/v1/tickets/search?status=2&status=3&priority=4&tag=refund&page=1", headers=HDR).json()
    assert r["total"] == 95 and r["has_more"] is True
    q = world.fd_calls[0].url.params
    assert q["query"] == "\"(status:2 OR status:3) AND priority:4 AND tag:'refund'\""
    world.script = [httpx.Response(200, json={"total": 500, "results": []})]
    assert c.get("/v1/tickets/search?status=2&page=10", headers=HDR).json()["has_more"] is False  # 300 cap


def test_search_requires_a_filter_and_rejects_double_quotes(make_client):
    c = make_client()
    assert c.get("/v1/tickets/search", headers=HDR).status_code == 422
    r = c.get('/v1/tickets/search?query=a"b', headers=HDR)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert c.get("/v1/tickets/search?tag=x'%20OR%20status:5", headers=HDR).status_code == 422


def test_private_notes_filtered_by_default(make_client, world):
    c = make_client()
    convs = [{"id": 1, "body_text": "public", "private": False},
             {"id": 2, "body_text": "internal secret", "private": True}]
    world.script = [httpx.Response(200, json=raw_ticket(5, conversations=convs))]
    r = c.get("/v1/tickets/5?include=conversations", headers=HDR).json()
    assert [x["id"] for x in r["conversations"]] == [1]
    c2 = make_client(include_private_notes=True)
    world.script = [httpx.Response(200, json=raw_ticket(6, conversations=convs))]
    assert len(c2.get("/v1/tickets/6?include=conversations", headers=HDR).json()["conversations"]) == 2


def test_cache_avoids_second_upstream_call_and_isolates_tenants(make_client, world):
    c = make_client()
    world.tenants["t2"] = ("other", "key-BBBB-2222")
    c.get("/v1/tickets/1", headers=HDR)
    c.get("/v1/tickets/1", headers=HDR)
    assert len(world.fd_calls) == 1
    c.get("/v1/tickets/1", headers={**HDR, "X-Tenant-Id": "t2"})
    assert len(world.fd_calls) == 2 and world.fd_calls[1].url.host == "other.freshdesk.com"


# ---------------- errors & auth ----------------
def test_adapter_token_and_tenant_header_required(make_client):
    c = make_client()
    assert c.get("/v1/tickets/1", headers={"X-Tenant-Id": "t1"}).status_code == 401
    assert c.get("/v1/tickets/1", headers={"X-Adapter-Token": "bad", "X-Tenant-Id": "t1"}).status_code == 401
    r = c.get("/v1/tickets/1", headers={"X-Adapter-Token": "adp"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_unknown_tenant(make_client):
    r = make_client().get("/v1/tickets/1", headers={**HDR, "X-Tenant-Id": "ghost"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "credentials_not_found"


def test_404_maps_to_not_found(make_client, world):
    world.script = [httpx.Response(404, json={})]
    r = make_client().get("/v1/tickets/999", headers=HDR)
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_per_page_bounds_validated(make_client):
    assert make_client().get("/v1/tickets?per_page=500", headers=HDR).status_code == 422


def test_401_refetches_rotated_key_once(make_client, world):
    def rotate_then_401(req):
        assert req.headers["authorization"] == basic("key-AAAA-1111")
        world.tenants["t1"] = ("acme", "key-NEW-9999")
        return httpx.Response(401, json={})
    world.script = [rotate_then_401]
    r = make_client().get("/v1/tickets/1", headers=HDR)
    assert r.status_code == 200
    assert world.fd_calls[1].headers["authorization"] == basic("key-NEW-9999")
    assert world.cred_calls == 2


def test_persistent_401_is_upstream_auth_error(make_client, world):
    world.script = [httpx.Response(401, json={}), httpx.Response(401, json={})]
    r = make_client().get("/v1/tickets/1", headers=HDR)
    assert r.status_code == 502 and r.json()["error"]["code"] == "upstream_auth_error"


def test_freshdesk_400_surfaces_message(make_client, world):
    world.script = [httpx.Response(400, json={"description": "Validation failed"})]
    r = make_client().get("/v1/tickets/search?status=2", headers=HDR)
    assert r.status_code == 422 and "Validation failed" in r.json()["error"]["message"]


def test_api_key_never_logged(make_client, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    c = make_client()
    c.get("/v1/tickets/1", headers=HDR)
    assert "key-AAAA-1111" not in caplog.text
