"""Bearer-token auth that resolves the *tenant* for every request.

Agent Studio is configured with one secret bearer token per workspace. We store only
SHA-256 hashes. The tenant is attached to the request context here, so tools have no
`tenant` argument an agent (or a prompt injection) could tamper with.
"""
from __future__ import annotations
import hashlib
import hmac
import logging
import re
from contextvars import ContextVar

from starlette.responses import JSONResponse

log = logging.getLogger("mcp-auth")
current_tenant: ContextVar[str | None] = ContextVar("current_tenant", default=None)
_HASH = re.compile(r"^[0-9a-f]{64}$")
_TENANT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def parse_tenant_tokens(raw: str) -> dict[str, str]:
    """'hash:tenant,hash:tenant' -> {hash: tenant}. Fails fast on malformed config."""
    out: dict[str, str] = {}
    for item in filter(None, (p.strip() for p in raw.split(","))):
        h, sep, tenant = item.partition(":")
        if not sep or not _HASH.match(h.lower()) or not _TENANT.match(tenant):
            raise ValueError("TENANT_TOKENS entries must look like <sha256hex>:<tenant_id>")
        out[h.lower()] = tenant
    if not out:
        raise ValueError("TENANT_TOKENS must define at least one tenant")
    return out


class TenantAuthMiddleware:
    def __init__(self, app, tokens: dict[str, str], public_paths: tuple[str, ...] = ("/healthz",)):
        self.app, self.tokens, self.public = app, tokens, public_paths

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in self.public:
            return await self.app(scope, receive, send)

        auth = dict(scope["headers"]).get(b"authorization", b"").decode("latin-1")
        scheme, _, token = auth.partition(" ")
        tenant = None
        if scheme.lower() == "bearer" and token:
            digest = hash_token(token.strip())
            for known, t in self.tokens.items():      # compare against all: no early exit
                if hmac.compare_digest(digest, known):
                    tenant = t
        if tenant is None:
            log.warning("rejected request: missing or invalid bearer token")
            resp = JSONResponse({"error": {"code": "unauthorized", "message": "Missing or invalid bearer token"}},
                                status_code=401, headers={"WWW-Authenticate": 'Bearer realm="freshdesk-connector"'})
            return await resp(scope, receive, send)

        reset = current_tenant.set(tenant)
        try:
            await self.app(scope, receive, send)
        finally:
            current_tenant.reset(reset)
