"""Freshdesk HTTP client: Basic auth, pagination, retries, rate-limit handling, caching.

Retry policy (GET only, so always safe to repeat):
  * 429            -> block the tenant for Retry-After (shared via the limiter), then wait & retry
                      if the wait is <= max_wait_seconds, otherwise raise RateLimitedError(retry_after)
                      so the *agent* decides what to do instead of us holding a connection open.
  * 500/502/503/504 and network errors -> exponential backoff with jitter, up to max_retries.
  * 401            -> refetch credentials once (key may have been rotated), then UpstreamAuthError.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import math
import random
import time
from typing import Any, Awaitable, Callable

import httpx

from common import (NotFoundError, RateLimitedError, SearchResult, Ticket, TicketList,
                    UpstreamAuthError, UpstreamError, ValidationError)

from .cache import Cache
from .config import Settings
from .credentials import CredentialClient
from .rate_limiter import Limiter

log = logging.getLogger("freshdesk-client")
RETRYABLE_5XX = {500, 502, 503, 504}
DEFAULT_RETRY_AFTER = 60.0
MAX_RETRY_AFTER = 3600.0
SEARCH_PAGE_SIZE = 30      # fixed by Freshdesk
SEARCH_MAX_PAGES = 10      # => at most 300 results per search


def parse_retry_after(value: str | None) -> float:
    try:
        return min(max(float(value), 0.0), MAX_RETRY_AFTER) if value is not None else DEFAULT_RETRY_AFTER
    except ValueError:
        return DEFAULT_RETRY_AFTER


class FreshdeskClient:
    def __init__(self, http: httpx.AsyncClient, creds: CredentialClient, limiter: Limiter,
                 cache: Cache, settings: Settings,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 rand: Callable[[], float] = random.random,
                 monotonic: Callable[[], float] = time.monotonic):
        self.http, self.creds, self.limiter, self.cache, self.s = http, creds, limiter, cache, settings
        self.sleep, self.rand, self.mono = sleep, rand, monotonic

    # ---------- public primitives ----------
    async def list_tickets(self, tenant: str, *, page: int = 1, per_page: int = 30,
                           filter: str | None = None, updated_since: str | None = None,
                           order_by: str | None = None, order_type: str | None = None,
                           include_description: bool = False) -> TicketList:
        params: dict[str, Any] = {"page": page, "per_page": per_page}
        for k, v in (("filter", filter), ("updated_since", updated_since),
                     ("order_by", order_by), ("order_type", order_type)):
            if v:
                params[k] = v
        if include_description:
            params["include"] = "description"

        async def load() -> dict:
            r = await self._request(tenant, "/tickets", params)
            raw = r.json()
            link = r.headers.get("link")
            has_more = ('rel="next"' in link) if link is not None else len(raw) >= per_page
            return TicketList(items=[self._ticket(x) for x in raw], page=page,
                              per_page=per_page, has_more=has_more).model_dump(mode="json")
        return TicketList.model_validate(await self._cached(tenant, "list", params, load))

    async def get_ticket(self, tenant: str, ticket_id: int, *, include: list[str] | None = None) -> Ticket:
        params = {"include": ",".join(sorted(include))} if include else {}

        async def load() -> dict:
            r = await self._request(tenant, f"/tickets/{ticket_id}", params)
            return self._ticket(r.json()).model_dump(mode="json")
        return Ticket.model_validate(await self._cached(tenant, f"get:{ticket_id}", params, load))

    async def search_tickets(self, tenant: str, query: str, *, page: int = 1) -> SearchResult:
        if page > SEARCH_MAX_PAGES:
            raise ValidationError(f"Freshdesk search supports at most {SEARCH_MAX_PAGES} pages "
                                  f"({SEARCH_PAGE_SIZE * SEARCH_MAX_PAGES} results); narrow the query")
        params = {"query": f'"{query}"', "page": page}

        async def load() -> dict:
            r = await self._request(tenant, "/search/tickets", params)
            body = r.json()
            total = int(body.get("total", 0))
            visible = min(total, SEARCH_PAGE_SIZE * SEARCH_MAX_PAGES)
            return SearchResult(items=[self._ticket(x) for x in body.get("results", [])],
                                total=total, page=page,
                                has_more=page < SEARCH_MAX_PAGES and page * SEARCH_PAGE_SIZE < visible
                                ).model_dump(mode="json")
        return SearchResult.model_validate(await self._cached(tenant, "search", params, load))

    # ---------- internals ----------
    def _ticket(self, raw: dict) -> Ticket:
        t = Ticket.from_freshdesk(raw)
        if t.conversations and not self.s.include_private_notes:
            t.conversations = [c for c in t.conversations if not c.private]
        return t

    async def _cached(self, tenant: str, kind: str, params: dict, load: Callable[[], Awaitable[dict]]) -> dict:
        key = "fd:cache:" + tenant + ":" + kind + ":" + hashlib.sha256(
            json.dumps(params, sort_keys=True).encode()).hexdigest()[:24]
        hit = await self.cache.get(key)
        if hit is not None:
            return hit
        value = await load()
        await self.cache.set(key, value, self.s.cache_ttl_seconds)
        return value

    async def _throttle(self, tenant: str) -> None:
        for _ in range(3):
            wait = await self.limiter.reserve(tenant)
            if wait <= 0:
                return
            if wait > self.s.max_wait_seconds:
                raise RateLimitedError(f"Rate limit reached for this Freshdesk account; retry in ~{math.ceil(wait)}s",
                                       retry_after=math.ceil(wait))
            await self.sleep(wait)
        raise RateLimitedError("Rate limit reached for this Freshdesk account", retry_after=self.s.max_wait_seconds)

    def _backoff(self, attempt: int) -> float:
        d = min(self.s.backoff_cap, self.s.backoff_base * (2 ** attempt))
        return d * (0.5 + 0.5 * self.rand())      # jitter in [d/2, d]

    async def _request(self, tenant: str, path: str, params: dict) -> httpx.Response:
        creds = await self.creds.get(tenant)
        attempt, refreshed = 0, False
        while True:
            await self._throttle(tenant)
            started = self.mono()
            try:
                resp = await self.http.get(
                    f"{self.s.freshdesk_base_url_template.format(domain=creds.domain)}/api/v2{path}", params=params,
                    auth=(creds.api_key.get_secret_value(), "X"),
                    timeout=self.s.request_timeout, follow_redirects=False)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                if attempt >= self.s.max_retries:
                    raise UpstreamError(f"Freshdesk unreachable: {type(e).__name__}") from e
                await self.sleep(self._backoff(attempt))
                attempt += 1
                continue

            log.info("freshdesk call", extra={"ctx": {
                "tenant": tenant, "path": path, "status": resp.status_code, "attempt": attempt,
                "ratelimit_remaining": resp.headers.get("x-ratelimit-remaining"),
                "ms": int((self.mono() - started) * 1000)}})

            sc = resp.status_code
            if sc == 200:
                return resp
            if sc == 401:
                if not refreshed:
                    self.creds.invalidate(tenant)
                    creds, refreshed = await self.creds.get(tenant), True
                    continue
                raise UpstreamAuthError("Freshdesk rejected the stored API key; re-register credentials for this tenant")
            if sc == 403:
                raise UpstreamAuthError("Freshdesk denied access (key lacks permission, API disabled, or account suspended)")
            if sc == 404:
                raise NotFoundError("Freshdesk resource not found")
            if sc == 429:
                ra = parse_retry_after(resp.headers.get("retry-after"))
                await self.limiter.block(tenant, ra)       # all replicas stop sending for this tenant
                attempt += 1
                if attempt > self.s.max_retries:
                    raise RateLimitedError("Freshdesk rate limit exceeded", retry_after=ra)
                continue                                    # _throttle waits, or sheds if wait is too long
            if sc in RETRYABLE_5XX:
                if attempt >= self.s.max_retries:
                    raise UpstreamError(f"Freshdesk returned {sc} after {attempt + 1} attempts")
                await self.sleep(self._backoff(attempt))
                attempt += 1
                continue
            if sc in (400, 422):
                raise ValidationError(f"Freshdesk rejected the request: {self._msg(resp)}")
            raise UpstreamError(f"Unexpected Freshdesk response: {sc}")

    @staticmethod
    def _msg(resp: httpx.Response) -> str:
        try:
            b = resp.json()
            return str(b.get("description") or b.get("message") or b)[:300]
        except Exception:
            return resp.text[:300]
