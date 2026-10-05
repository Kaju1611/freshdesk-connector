# Freshdesk connector for Agent Studio

A private, multi-tenant, **read-only** connector that lets an Agent Studio agent list, fetch and search
Freshdesk tickets over MCP. Built as small stateless services so each piece scales on its own.

```
Agent Studio ──MCP/HTTPS + bearer──▶ mcp-server ──▶ freshdesk-adapter ──▶ Freshdesk API
 (one token per workspace)           (tools, tenant   │   │  (retries, 429 handling,
                                      from token)     │   └─▶ Redis: shared rate limits + cache
                                                      └─▶ credential-service ──▶ encrypted key store
```

| Service | Port | Responsibility |
|---|---|---|
| `mcp-server` | 8003 | MCP tools, bearer auth → tenant, LLM-friendly output and error messages |
| `freshdesk-adapter` | 8002 (internal) | Freshdesk HTTP client: pagination, retries, rate limiting, caching |
| `credential-service` | 8001 | Encrypted per-tenant Freshdesk domain + API key; key verification on onboarding |
| `libs/common` | – | Shared models, typed errors, secret-redacting JSON logs |

What the agent can and cannot do: **[docs/agent-capabilities.md](docs/agent-capabilities.md)**.
Machine-readable tool spec (generated from the code): **[docs/mcp-tool-spec.json](docs/mcp-tool-spec.json)**.

## Quick start

```bash
cp .env.example .env
make gen-key                                   # paste into MASTER_KEYS in .env
# set ADMIN_TOKEN / INTERNAL_TOKEN / ADAPTER_TOKEN to long random values

make mint-token TENANT=acme                    # prints TOKEN (for Agent Studio) + a TENANT_TOKENS entry
# paste the TENANT_TOKENS entry into .env  (comma-separate several tenants)

docker compose up --build
```

**Onboard a customer.** The credential service checks the key against Freshdesk before storing it:
```bash
curl -X PUT http://localhost:8001/v1/tenants/acme/freshdesk \
  -H "X-Admin-Token: $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"domain": "acme", "api_key": "<Freshdesk API key>"}'
```
`acme` here must match the tenant id used in `make mint-token`. Use a Freshdesk API key from an agent with
read access to tickets (Profile settings → API key).

**Smoke test the MCP endpoint:**
```bash
curl -s http://localhost:8003/mcp -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_tickets","arguments":{"per_page":3}}}'
```

**Connect Agent Studio.** Add a remote MCP server with URL `https://<your-host>/mcp` and the header
`Authorization: Bearer <TOKEN>`. Put TLS in front of `mcp-server` (Caddy, nginx or a cloud load balancer);
it is the only service that should be reachable from outside. *(Exact field names depend on Agent Studio's
connector screen, which I have not been able to check.)*

**Rotate a customer's Freshdesk key:** `PUT` the new key to the same URL. The adapter recovers on its own
(a 401 triggers one credential refetch). **Offboard:** `DELETE /v1/tenants/acme`; remove the `TENANT_TOKENS` entry.

## Configuration (environment)

| Variable | Service | Meaning |
|---|---|---|
| `MASTER_KEYS` | cred | Fernet key(s), newest first. Add a new key at the front, then call `rotate_all()`. |
| `ADMIN_TOKEN` | cred | Register/delete tenants. |
| `INTERNAL_TOKEN` | cred, adapter | Adapter → credential-service. Only this token can read decrypted keys. |
| `ADAPTER_TOKEN` | adapter, mcp | mcp-server → adapter. |
| `TENANT_TOKENS` | mcp | `sha256(token):tenant_id` pairs. Tokens themselves are never stored. |
| `ALLOWED_HOSTS` | mcp | Public hostnames (Host-header check). Empty = off. |
| `REDIS_URL` | adapter | Shared rate limits + cache. **Required for more than one adapter replica.** |
| `TENANT_RPM` | adapter | Per-tenant requests/minute budget; keep below your Freshdesk plan's limit (default 100). |
| `MAX_WAIT_SECONDS` | adapter | Longest the adapter sleeps for a rate limit before returning `rate_limited` (default 8). |
| `CACHE_TTL_SECONDS` | adapter | Response cache (default 30; 0 disables). |
| `INCLUDE_PRIVATE_NOTES` | adapter | Default `false`. |

## Scaling
`mcp-server` is stateless (no MCP sessions) and `freshdesk-adapter` keeps its shared state in Redis, so both
scale by adding replicas: `docker compose up --scale freshdesk-adapter=3 --scale mcp-server=2` (put a load
balancer in front of mcp-server). One tenant hitting its Freshdesk limit is blocked for everyone's replicas and
does not affect other tenants. `credential-service` is low-traffic (the adapter caches credentials for 60 s).

## Tests
```bash
make test          # unit tests per service + end-to-end
make spec          # regenerate docs/mcp-tool-spec.json after changing tools (a test fails if it is stale)
```
| Layer | What it proves |
|---|---|
| credential-service (13) | Encryption at rest, key rotation, token separation, key verification gate |
| freshdesk-adapter (34) | Pagination, query building, 429 / Retry-After, backoff, shared Redis limiter, caching, key refetch |
| mcp-server (21) | Real MCP protocol over HTTP, tenant-from-token (incl. 40 concurrent mixed-tenant calls), error wording, spec drift |
| end-to-end (23) | Four real processes + official MCP client against a stub Freshdesk: onboarding, list/get/search, tenant isolation, 429 wait and shed, 5xx retry, key rotation, no secrets in logs |

## Production checklist
- [ ] Replace SQLite in `credential-service/app/store.py` with Postgres or a cloud secret manager (SQLite is single-node). The store interface is 5 methods.
- [ ] Don't publish port 8001 beyond your onboarding tooling; keep 8002 internal.
- [ ] Generate long random `ADMIN_/INTERNAL_/ADAPTER_TOKEN`s and per-tenant tokens; keep `.env`/`MASTER_KEYS` in a secret manager.
- [ ] Set `REDIS_URL` and `ALLOWED_HOSTS`; terminate TLS in front of `mcp-server`.
- [ ] Set `TENANT_RPM` from each customer's Freshdesk plan.
- [ ] Decide whether customer ticket content (personal data) may be sent to the agent's model.
- [ ] Run a smoke test against a **real** Freshdesk account (below).

## What is and isn't verified
Verified: everything in the table above, including the full request path across real processes.
**Not verified:** behaviour against the real Freshdesk API and Agent Studio. The stub in
`tests/integration/stub_freshdesk.py` is written from Freshdesk's public docs (30-day default window, search
syntax and 300-result cap, `Link` pagination, 429 + `Retry-After`), so check these on a real account first:
list with and without `updated_since`; a search using `status`, `tag` and a date range; `include=conversations`
on a long ticket; and a forced 429. Docker images were not built in the authoring environment; run
`docker compose up --build` once.

## Adapting to another tool
Only `freshdesk-adapter` (and the models in `libs/common`) knows about Freshdesk. For WooCommerce,
Zoho Inventory or Unicommerce, write a new adapter with the same list/get/search routes and rate-limit
handling, and change the tool definitions in `mcp-server/app/tools.py`.
