"""A small stand-in for the Freshdesk API, for end-to-end tests ONLY.

It models the behaviours the connector depends on (per-account Basic auth, the 30-day default
window, Link-header pagination, search syntax + 30/page, private notes, 429/5xx injection).
It is written from the public Freshdesk docs, so it proves the services work together;
it does NOT prove Freshdesk itself behaves identically. Always smoke-test against a real account.
"""
import base64
import re
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()
DOMAINS: dict[str, dict] = {}      # domain -> {"key": str, "tickets": [..]}
FAILS: dict[str, list] = {}        # domain -> queued failure responses
LOG: list[dict] = []               # every API request seen


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if "T" in s else \
        datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


# ------------------------------ admin ------------------------------
@app.post("/__admin/domains/{domain}")
async def seed(domain: str, request: Request):
    b = await request.json()
    DOMAINS[domain] = {"key": b["key"], "tickets": b.get("tickets", [])}
    FAILS.pop(domain, None)
    return {"ok": True}


@app.post("/__admin/domains/{domain}/key")
async def rotate(domain: str, request: Request):
    DOMAINS[domain]["key"] = (await request.json())["key"]
    return {"ok": True}


@app.post("/__admin/domains/{domain}/fail")
async def queue_fail(domain: str, request: Request):
    FAILS.setdefault(domain, []).extend((await request.json())["responses"])
    return {"ok": True}


@app.get("/__admin/log")
async def log(domain: str | None = None):
    return [x for x in LOG if domain in (None, x["domain"])]


@app.get("/healthz")
async def health():
    return {"ok": True}


# ------------------------------ Freshdesk API ------------------------------
def _auth(domain: str, request: Request):
    d = DOMAINS.get(domain)
    h = request.headers.get("authorization", "")
    if not d or not h.startswith("Basic "):
        return None, None
    try:
        user, _, _ = base64.b64decode(h[6:]).decode().partition(":")
    except Exception:
        return d, None
    return d, user


def _record(domain, request, status, key):
    LOG.append({"domain": domain, "path": request.url.path.split("/api/v2")[-1],
                "status": status, "key": key})


def _gate(domain: str, request: Request):
    """Failure injection, then auth. Returns (domain_state, error_response|None)."""
    d, key = _auth(domain, request)
    if FAILS.get(domain):
        f = FAILS[domain].pop(0)
        headers = {"Retry-After": str(f["retry_after"])} if "retry_after" in f else {}
        _record(domain, request, f["status"], key)
        return d, JSONResponse({"description": "injected"}, status_code=f["status"], headers=headers)
    if d is None or key != d["key"]:
        _record(domain, request, 401, key)
        return d, JSONResponse({"code": "invalid_credentials"}, status_code=401)
    return d, None


def _ok(domain, request, key_state, body, headers=None):
    _record(domain, request, 200, key_state["key"])
    return JSONResponse(body, headers=headers or {})


@app.get("/{domain}/api/v2/tickets")
async def list_tickets(domain: str, request: Request, page: int = 1, per_page: int = 30,
                       updated_since: str | None = None):
    d, err = _gate(domain, request)
    if err:
        return err
    if updated_since:
        rows = [t for t in d["tickets"] if _parse(t["updated_at"]) >= _parse(updated_since)]
    else:   # real Freshdesk: only tickets created in the last 30 days by default
        cutoff = _now() - timedelta(days=30)
        rows = [t for t in d["tickets"] if _parse(t["created_at"]) >= cutoff]
    rows.sort(key=lambda t: t["updated_at"], reverse=True)
    chunk = rows[(page - 1) * per_page: page * per_page]
    headers = {}
    if page * per_page < len(rows):
        headers["Link"] = f'<http://stub/{domain}/api/v2/tickets?page={page + 1}>; rel="next"'
    return _ok(domain, request, d, [{k: v for k, v in t.items() if k not in ("conversations", "requester")}
                                    for t in chunk], headers)


@app.get("/{domain}/api/v2/tickets/{ticket_id}")
async def get_ticket(domain: str, ticket_id: int, request: Request, include: str | None = None):
    d, err = _gate(domain, request)
    if err:
        return err
    t = next((x for x in d["tickets"] if x["id"] == ticket_id), None)
    if not t:
        _record(domain, request, 404, d["key"])
        return JSONResponse({"code": "resource_not_found"}, status_code=404)
    wanted = set((include or "").split(","))
    out = {k: v for k, v in t.items() if k not in ("conversations", "requester")}
    if "conversations" in wanted:
        out["conversations"] = t.get("conversations", [])
    if "requester" in wanted:
        out["requester"] = t.get("requester")
    return _ok(domain, request, d, out)


def _clause_match(t, clause: str) -> bool:
    clause = clause.strip()
    if clause.startswith("(") and clause.endswith(")"):
        return any(_clause_match(t, c) for c in clause[1:-1].split(" OR "))
    m = re.fullmatch(r"(\w+):(>|<)?'?([^']*)'?", clause)
    if not m:
        raise ValueError(f"unsupported clause: {clause}")
    field, op, val = m.groups()
    if field in ("status", "priority", "group_id", "agent_id"):
        actual = t.get("responder_id" if field == "agent_id" else field)
        return actual == int(val)
    if field == "tag":
        return val in t.get("tags", [])
    if field == "type":
        return t.get("type") == val
    if field in ("created_at", "updated_at"):
        day, bound = _parse(t[field]).date(), _parse(val).date()
        return day > bound if op == ">" else day < bound
    raise ValueError(f"unsupported field: {field}")


@app.get("/{domain}/api/v2/search/tickets")
async def search(domain: str, request: Request, query: str, page: int = 1):
    d, err = _gate(domain, request)
    if err:
        return err
    if not (query.startswith('"') and query.endswith('"')):
        _record(domain, request, 400, d["key"])
        return JSONResponse({"description": "query must be wrapped in double quotes"}, status_code=400)
    if len(query) > 512 + 2 or page > 10:
        _record(domain, request, 400, d["key"])
        return JSONResponse({"description": "Validation failed"}, status_code=400)
    try:
        rows = [t for t in d["tickets"] if all(_clause_match(t, c) for c in query[1:-1].split(" AND "))]
    except ValueError as e:
        _record(domain, request, 400, d["key"])
        return JSONResponse({"description": str(e)}, status_code=400)
    rows.sort(key=lambda t: t["updated_at"], reverse=True)
    rows = rows[:300]
    chunk = rows[(page - 1) * 30: page * 30]
    return _ok(domain, request, d, {"total": len(rows), "results": [
        {k: v for k, v in t.items() if k not in ("conversations", "requester")} for t in chunk]})
