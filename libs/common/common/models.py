"""Shared data models: normalized ticket shapes + credential payloads."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, SecretStr, field_validator

from .errors import ValidationError

STATUS_NAMES = {2: "open", 3: "pending", 4: "resolved", 5: "closed"}
PRIORITY_NAMES = {1: "low", 2: "medium", 3: "high", 4: "urgent"}

# A Freshdesk subdomain: DNS label only. Prevents SSRF via crafted "domains".
_SUBDOMAIN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def normalize_domain(value: str) -> str:
    """Accept 'acme', 'acme.freshdesk.com' or 'https://acme.freshdesk.com/...' -> 'acme'."""
    v = value.strip().lower()
    v = re.sub(r"^https?://", "", v).split("/")[0]
    if v.endswith(".freshdesk.com"):
        v = v[: -len(".freshdesk.com")]
    if not _SUBDOMAIN.match(v):
        raise ValidationError("Invalid Freshdesk domain; expected e.g. 'acme' or 'acme.freshdesk.com'")
    return v


# ---------- credentials ----------
class TenantRegistration(BaseModel):
    domain: str
    api_key: SecretStr = Field(min_length=8)

    @field_validator("domain")
    @classmethod
    def _domain(cls, v: str) -> str:
        return normalize_domain(v)


class TenantInfo(BaseModel):
    """Safe-to-return metadata. Never contains the key."""
    tenant_id: str
    domain: str
    key_last4: str
    created_at: datetime
    updated_at: datetime


class TenantCredentials(BaseModel):
    """Internal only: returned to the adapter, never to agents or logs."""
    tenant_id: str
    domain: str
    api_key: SecretStr


# ---------- tickets ----------
class Requester(BaseModel):
    id: int | None = None
    name: str | None = None
    email: str | None = None


class Conversation(BaseModel):
    id: int
    body_text: str | None = None
    incoming: bool | None = None
    private: bool = False
    user_id: int | None = None
    from_email: str | None = None
    created_at: datetime | None = None


class Ticket(BaseModel):
    id: int
    subject: str
    status: int
    status_name: str
    priority: int
    priority_name: str
    requester_id: int | None = None
    responder_id: int | None = None
    group_id: int | None = None
    type: str | None = None
    tags: list[str] = []
    description_text: str | None = None
    created_at: datetime
    updated_at: datetime
    due_by: datetime | None = None
    requester: Requester | None = None
    conversations: list[Conversation] | None = None

    @classmethod
    def from_freshdesk(cls, raw: dict[str, Any]) -> "Ticket":
        return cls(
            id=raw["id"],
            subject=raw.get("subject") or "",
            status=raw["status"],
            status_name=STATUS_NAMES.get(raw["status"], "custom"),
            priority=raw["priority"],
            priority_name=PRIORITY_NAMES.get(raw["priority"], "unknown"),
            requester_id=raw.get("requester_id"),
            responder_id=raw.get("responder_id"),
            group_id=raw.get("group_id"),
            type=raw.get("type"),
            tags=raw.get("tags") or [],
            description_text=raw.get("description_text"),
            created_at=raw["created_at"],
            updated_at=raw["updated_at"],
            due_by=raw.get("due_by"),
            requester=raw.get("requester"),
            conversations=raw.get("conversations"),
        )


class TicketList(BaseModel):
    items: list[Ticket]
    page: int
    per_page: int
    has_more: bool


class SearchResult(BaseModel):
    items: list[Ticket]
    total: int
    page: int
    has_more: bool
