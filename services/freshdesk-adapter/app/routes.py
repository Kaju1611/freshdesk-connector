from __future__ import annotations
import hmac
from typing import Literal

from fastapi import APIRouter, Depends, Header, Query, Request

from common import AuthError, SearchResult, Ticket, TicketList
from .freshdesk_client import FreshdeskClient
from .query_builder import build_search_query

router = APIRouter(prefix="/v1")


def _client(request: Request) -> FreshdeskClient:
    return request.app.state.client


def authorize(request: Request, x_adapter_token: str | None = Header(default=None)) -> None:
    expected = request.app.state.settings.adapter_token
    if not x_adapter_token or not hmac.compare_digest(x_adapter_token, expected):
        raise AuthError("Missing or invalid adapter token")


def tenant(x_tenant_id: str = Header(..., pattern=r"^[A-Za-z0-9_-]{1,64}$")) -> str:
    return x_tenant_id


Auth = Depends(authorize)


@router.get("/tickets", response_model=TicketList, dependencies=[Auth])
async def list_tickets(
    tenant_id: str = Depends(tenant), client: FreshdeskClient = Depends(_client),
    page: int = Query(1, ge=1, le=300), per_page: int = Query(30, ge=1, le=100),
    filter: Literal["new_and_my_open", "watching", "spam", "deleted"] | None = None,
    updated_since: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}(T[\d:.]+Z)?$"),
    order_by: Literal["created_at", "due_by", "updated_at", "status"] | None = None,
    order_type: Literal["asc", "desc"] | None = None,
    include_description: bool = False,
):
    return await client.list_tickets(tenant_id, page=page, per_page=per_page, filter=filter,
                                     updated_since=updated_since, order_by=order_by,
                                     order_type=order_type, include_description=include_description)


# NOTE: declared before /tickets/{ticket_id} so "search" is never parsed as an id.
@router.get("/tickets/search", response_model=SearchResult, dependencies=[Auth])
async def search_tickets(
    tenant_id: str = Depends(tenant), client: FreshdeskClient = Depends(_client),
    query: str | None = Query(None, max_length=400),
    status: list[int] | None = Query(None), priority: list[int] | None = Query(None),
    tag: str | None = None, type: str | None = None,
    group_id: int | None = None, agent_id: int | None = None,
    created_after: str | None = None, created_before: str | None = None,
    updated_after: str | None = None, updated_before: str | None = None,
    page: int = Query(1, ge=1, le=10),
):
    q = build_search_query(query=query, status=status, priority=priority, tag=tag, type=type,
                           group_id=group_id, agent_id=agent_id, created_after=created_after,
                           created_before=created_before, updated_after=updated_after,
                           updated_before=updated_before)
    return await client.search_tickets(tenant_id, q, page=page)


@router.get("/tickets/{ticket_id}", response_model=Ticket, dependencies=[Auth])
async def get_ticket(
    ticket_id: int, tenant_id: str = Depends(tenant), client: FreshdeskClient = Depends(_client),
    include: list[Literal["conversations", "requester"]] | None = Query(None),
):
    return await client.get_ticket(tenant_id, ticket_id, include=include)
