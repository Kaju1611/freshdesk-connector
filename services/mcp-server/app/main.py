from __future__ import annotations
import os
from contextlib import asynccontextmanager

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse

from common.logging import configure_logging

from .adapter_client import AdapterClient
from .auth import TenantAuthMiddleware, parse_tenant_tokens
from .config import Settings
from .tools import INSTRUCTIONS, register_tools


def create_app(settings: Settings | None = None, *, adapter_http: httpx.AsyncClient | None = None) -> Starlette:
    settings = settings or Settings()  # type: ignore[call-arg]
    configure_logging("mcp-server", settings.log_level)
    tokens = parse_tenant_tokens(settings.tenant_tokens)          # fail fast on bad config

    http = adapter_http or httpx.AsyncClient(timeout=settings.adapter_timeout)
    server = MCPServer("freshdesk-connector", instructions=INSTRUCTIONS, version="0.1.0")
    register_tools(server, AdapterClient(http, settings.adapter_url, settings.adapter_token), settings)

    hosts = [h.strip() for h in settings.allowed_hosts.split(",") if h.strip()]
    # Stateless + JSON responses: no server-side sessions, so any replica can serve any request.
    app = server.streamable_http_app(
        json_response=True, stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=bool(hosts),
                                                     allowed_hosts=hosts))

    async def healthz(_):
        return JSONResponse({"status": "ok"})
    app.add_route("/healthz", healthz)
    app.add_middleware(TenantAuthMiddleware, tokens=tokens)

    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a):
        async with inner(a):
            yield
        if adapter_http is None:
            await http.aclose()
    app.router.lifespan_context = lifespan
    return app


app = create_app() if os.environ.get("ADAPTER_TOKEN") and os.environ.get("TENANT_TOKENS") else None
