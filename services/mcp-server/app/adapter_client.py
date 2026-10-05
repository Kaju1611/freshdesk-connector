"""Thin client for freshdesk-adapter. Knows nothing about Freshdesk."""
from __future__ import annotations
from typing import Any

import httpx


class AdapterError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False,
                 retry_after: float | None = None, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable
        self.retry_after, self.details = retry_after, details or {}


class AdapterClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, token: str):
        self.http, self.base, self.token = http, base_url.rstrip("/"), token

    async def get(self, tenant_id: str, path: str, params: dict[str, Any] | None = None) -> dict:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            r = await self.http.get(self.base + path, params=clean,
                                    headers={"X-Adapter-Token": self.token, "X-Tenant-Id": tenant_id})
        except httpx.TimeoutException as e:
            raise AdapterError("adapter_timeout", "The Freshdesk connector timed out", retryable=True) from e
        except httpx.HTTPError as e:
            raise AdapterError("adapter_unavailable", "The Freshdesk connector is unreachable", retryable=True) from e
        if r.status_code == 200:
            return r.json()
        try:
            err = r.json()["error"]
        except Exception:
            raise AdapterError("upstream_error", f"Connector returned HTTP {r.status_code}", retryable=r.status_code >= 500)
        d = err.get("details") or {}
        ra = d.get("retry_after_seconds")
        if ra is None and r.headers.get("retry-after", "").isdigit():
            ra = float(r.headers["retry-after"])
        return_err = AdapterError(err.get("code", "upstream_error"), err.get("message", "Unknown error"),
                                  retryable=bool(err.get("retryable")), retry_after=ra, details=d)
        raise return_err
