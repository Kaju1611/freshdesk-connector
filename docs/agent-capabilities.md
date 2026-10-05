# Freshdesk connector: what the agent can and cannot do

The connector gives an Agent Studio agent **read-only** access to one workspace's Freshdesk tickets
through three MCP tools: `list_tickets`, `get_ticket`, `search_tickets`
(full schemas: [`mcp-tool-spec.json`](mcp-tool-spec.json)).

## The agent CAN
- **Browse** tickets page by page, sorted by creation date, update date, due date or status.
- **Search** by status, priority, tag, type, group, assignee, created/updated date ranges, or a raw
  Freshdesk query for advanced cases. Combined filters are ANDed.
- **Read one ticket** in full: subject, status, priority, type, tags, requester/assignee/group ids, dates,
  description text, and (on request) the **public** conversation thread and the requester's name and email.
- **Recover from rate limits** without help: short waits are handled automatically; longer ones come back as a
  clear "wait N seconds" message.

## The agent CANNOT
- **Change anything.** No creating, replying, editing, assigning, closing, merging, deleting or adding notes.
  There is no write code path in the connector.
- **See private/internal notes.** They are filtered out before the agent sees them (operator setting
  `INCLUDE_PRIVATE_NOTES`, off by default).
- **Open attachments**, or read custom fields, contacts, companies, agent profiles, SLAs or satisfaction ratings.
  Ids (requester, agent, group) are returned as numbers and are not resolved to names, except the requester
  via `include_requester`.
- **Filter by custom statuses.** Status names are `open`, `pending`, `resolved`, `closed`; other statuses
  show up as `custom`.
- **Produce reports.** There are no aggregates beyond the match count in a search.
- **Reach another workspace.** The workspace is fixed by the bearer token; tools take no tenant argument.
- **Work with other tools** (orders, inventory, other helpdesks): this connector is Freshdesk tickets only.

## Limits the agent should know about
| Topic | Behaviour |
|---|---|
| Default window | `list_tickets` only returns tickets **created in the last 30 days** unless `updated_since` is set. The result says so. |
| Search size | 30 results per page, 10 pages max: only the **first 300 matches** are reachable. The result warns when there are more; narrow the filters. |
| Page size | `per_page` up to 100 for list. |
| Long text | Descriptions are clipped at 1,500 characters and each reply at 1,000, flagged `"truncated": true`. |
| Long threads | Freshdesk may embed only the first ~10 conversations of a long ticket. |
| Freshness | Responses may be up to 30 seconds old (cache). Search results may trail very recent changes. |
| Rate limits | Waits of up to 8 s are absorbed automatically. Beyond that the tool returns `rate_limited` with the wait time. Limits are per Freshdesk account and shared by every agent using it. |

## Errors the agent will see
`rate_limited`, `not_found`, `not_configured`, `auth_failed`, `invalid_request`, `temporarily_unavailable`.
Each message says whether to retry and what to tell the user (table in the tool spec).

## Safety notes for whoever deploys this
- **Ticket text is untrusted.** Customers write it. Every ticket result carries a notice telling the agent to treat
  it as data. Being read-only, a malicious ticket cannot change Freshdesk, but it can try to steer the agent,
  so be careful pairing this connector with tools that send messages or touch other systems.
- **Customer personal data reaches the model.** Names, emails and message bodies from tickets are passed to the
  agent. Confirm that is acceptable under your privacy terms before enabling it.
- **Credentials** are stored encrypted, returned only to the internal adapter, and never sent to the agent or logged.
