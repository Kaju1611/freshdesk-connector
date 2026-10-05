"""Builds Freshdesk search-query strings from structured filters.

Freshdesk syntax: the whole query is wrapped in double quotes; string values use single
quotes; e.g.  "status:2 AND priority:3 AND tag:'refund'". Structured input is validated
so agents can't produce malformed queries.
"""
from __future__ import annotations
import re

from common import ValidationError

_SAFE = re.compile(r"^[\w\-. @]{1,64}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_LEN = 512


def _ints(field: str, values: list[int]) -> str:
    items = [f"{field}:{int(v)}" for v in values]
    return items[0] if len(items) == 1 else "(" + " OR ".join(items) + ")"


def _text(field: str, value: str) -> str:
    if not _SAFE.match(value):
        raise ValidationError(f"Invalid value for '{field}': letters, digits, space and - . _ @ only (max 64)")
    return f"{field}:'{value}'"


def _date(field: str, op: str, value: str) -> str:
    if not _DATE.match(value):
        raise ValidationError(f"'{field}' dates must be YYYY-MM-DD")
    return f"{field}:{op}'{value}'"


def build_search_query(*, query: str | None = None, status: list[int] | None = None,
                       priority: list[int] | None = None, tag: str | None = None,
                       type: str | None = None, group_id: int | None = None,
                       agent_id: int | None = None, created_after: str | None = None,
                       created_before: str | None = None, updated_after: str | None = None,
                       updated_before: str | None = None) -> str:
    parts: list[str] = []
    if query and query.strip():
        q = query.strip()
        if '"' in q or "\\" in q:
            raise ValidationError("'query' must not contain double quotes or backslashes; use single quotes for values")
        parts.append(f"({q})")
    if status:
        parts.append(_ints("status", status))
    if priority:
        parts.append(_ints("priority", priority))
    if tag:
        parts.append(_text("tag", tag))
    if type:
        parts.append(_text("type", type))
    if group_id is not None:
        parts.append(f"group_id:{int(group_id)}")
    if agent_id is not None:
        parts.append(f"agent_id:{int(agent_id)}")
    if created_after:
        parts.append(_date("created_at", ">", created_after))
    if created_before:
        parts.append(_date("created_at", "<", created_before))
    if updated_after:
        parts.append(_date("updated_at", ">", updated_after))
    if updated_before:
        parts.append(_date("updated_at", "<", updated_before))
    if not parts:
        raise ValidationError("Provide at least one search filter or a 'query'")
    out = " AND ".join(parts)
    if len(out) > MAX_LEN:
        raise ValidationError(f"Search query too long ({len(out)} > {MAX_LEN} chars)")
    return out
