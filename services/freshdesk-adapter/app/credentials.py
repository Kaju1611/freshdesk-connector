"""Fetches per-tenant Freshdesk credentials from credential-service (short in-memory TTL)."""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx
from pydantic import SecretStr

from common import CredentialNotFoundError, UpstreamError


@dataclass(frozen=True)
class Creds:
    domain: str
    api_key: SecretStr = field(repr=False)


class CredentialClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, token: str, ttl: int,
                 now: Callable[[], float] = time.monotonic):
        self.http, self.base, self.token, self.ttl, self.now = http, base_url.rstrip("/"), token, ttl, now
        self._c: dict[str, tuple[float, Creds]] = {}

    async def get(self, tenant_id: str) -> Creds:
        hit = self._c.get(tenant_id)
        if hit and hit[0] > self.now():
            return hit[1]
        try:
            r = await self.http.get(f"{self.base}/internal/v1/tenants/{tenant_id}/credentials",
                                    headers={"X-Internal-Token": self.token}, timeout=5)
        except httpx.HTTPError as e:
            raise UpstreamError(f"credential-service unreachable: {type(e).__name__}") from e
        if r.status_code == 404:
            raise CredentialNotFoundError(f"No Freshdesk credentials registered for tenant '{tenant_id}'")
        if r.status_code != 200:
            raise UpstreamError(f"credential-service returned {r.status_code}")
        d = r.json()
        creds = Creds(domain=d["domain"], api_key=SecretStr(d["api_key"]))
        self._c[tenant_id] = (self.now() + self.ttl, creds)
        return creds

    def invalidate(self, tenant_id: str) -> None:
        self._c.pop(tenant_id, None)
