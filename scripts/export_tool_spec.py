#!/usr/bin/env python
"""Generate docs/mcp-tool-spec.json from the LIVE tool definitions (so it cannot drift).

    python scripts/export_tool_spec.py            # write docs/mcp-tool-spec.json
    python scripts/export_tool_spec.py --check    # exit 1 if the file is stale (CI)
"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "mcp-tool-spec.json"

ERRORS = [
    {"code": "rate_limited", "retryable": True,
     "meaning": "Freshdesk is throttling this account.",
     "agent_should": "Wait the number of seconds stated in the message, then retry. Use narrower filters."},
    {"code": "not_found", "retryable": False,
     "meaning": "The ticket does not exist or the API key cannot see it.",
     "agent_should": "Do not retry; tell the user."},
    {"code": "not_configured", "retryable": False,
     "meaning": "No Freshdesk account is connected for this workspace.",
     "agent_should": "Tell the user an administrator must connect Freshdesk."},
    {"code": "auth_failed", "retryable": False,
     "meaning": "Freshdesk rejected the stored API key (revoked, rotated, or lacking permission).",
     "agent_should": "Do not retry; tell the user an administrator must re-register the key."},
    {"code": "invalid_request", "retryable": False,
     "meaning": "Arguments were rejected (bad date, unsupported query, no filter on search).",
     "agent_should": "Fix the arguments named in the message and call again."},
    {"code": "temporarily_unavailable", "retryable": True,
     "meaning": "Freshdesk or the connector had a transient failure (after automatic retries).",
     "agent_should": "Retry once after a short delay; if it fails again, tell the user."},
]


def build_spec() -> dict:
    sys.path[:0] = [str(ROOT / "services" / "mcp-server"), str(ROOT / "libs" / "common")]
    import httpx
    from mcp.server.mcpserver import MCPServer
    from app.adapter_client import AdapterClient
    from app.config import Settings
    from app.tools import INSTRUCTIONS, register_tools

    server = MCPServer("freshdesk-connector", instructions=INSTRUCTIONS, version="0.1.0")
    register_tools(server, AdapterClient(httpx.AsyncClient(), "http://unused", "unused"),
                   Settings(adapter_token="unused", tenant_tokens="unused"))
    tools = asyncio.run(server.list_tools())
    return {
        "spec_version": 1,
        "server": {"name": "freshdesk-connector", "version": "0.1.0"},
        "transport": {"type": "streamable-http", "endpoint_path": "/mcp", "stateless": True,
                      "response_mode": "json", "health_check": "/healthz (unauthenticated)"},
        "authentication": {
            "type": "http", "scheme": "bearer",
            "description": "One secret bearer token per workspace (Authorization: Bearer <token>). The server "
                           "derives the tenant from the token; tools take no tenant argument."},
        "instructions": INSTRUCTIONS,
        "tools": sorted((t.model_dump(mode="json", exclude_none=True, by_alias=True) for t in tools),
                        key=lambda t: t["name"]),
        "errors": ERRORS,
    }


if __name__ == "__main__":
    text = json.dumps(build_spec(), indent=2, ensure_ascii=False) + "\n"
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text() != text:
            sys.exit("docs/mcp-tool-spec.json is stale: run python scripts/export_tool_spec.py")
        print("tool spec is up to date")
    else:
        OUT.write_text(text)
        print(f"wrote {OUT.relative_to(ROOT)} ({len(text)} bytes)")
