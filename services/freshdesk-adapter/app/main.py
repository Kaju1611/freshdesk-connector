from __future__ import annotations
import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from common import ConnectorError, RateLimitedError, ValidationError
from common.logging import configure_logging

from .cache import InMemoryCache, RedisCache
from .config import Settings
from .credentials import CredentialClient
from .freshdesk_client import FreshdeskClient
from .rate_limiter import InMemoryLimiter, RedisLimiter
from .routes import router

log = logging.getLogger("freshdesk-adapter")


def create_app(settings: Settings | None = None, *, http_client: httpx.AsyncClient | None = None,
               limiter=None, cache=None, **client_kwargs) -> FastAPI:
    """`client_kwargs` (sleep, rand, monotonic) exist so tests can control time."""
    settings = settings or Settings()  # type: ignore[call-arg]
    configure_logging("freshdesk-adapter", settings.log_level)

    http = http_client or httpx.AsyncClient()
    redis = None
    if settings.redis_url and (limiter is None or cache is None):
        import redis.asyncio as aioredis
        redis = aioredis.from_url(settings.redis_url)
    limiter = limiter or (RedisLimiter(redis, settings.tenant_rpm) if redis
                          else InMemoryLimiter(settings.tenant_rpm))
    cache = cache or (RedisCache(redis) if redis else InMemoryCache())
    if not redis:
        log.warning("REDIS_URL not set: rate limits and cache are per-process (do not run >1 replica)")

    creds = CredentialClient(http, settings.credential_service_url, settings.internal_token,
                             settings.creds_ttl_seconds)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        if http_client is None:
            await http.aclose()
        if redis:
            await redis.aclose()

    app = FastAPI(title="freshdesk-adapter", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.client = FreshdeskClient(http, creds, limiter, cache, settings, **client_kwargs)
    app.include_router(router)

    @app.exception_handler(ConnectorError)
    async def _connector_error(_: Request, exc: ConnectorError):
        headers = {}
        if isinstance(exc, RateLimitedError) and exc.retry_after:
            headers["Retry-After"] = str(int(exc.retry_after))
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict(), headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError):
        errs = [{"loc": [str(x) for x in e["loc"]], "msg": e["msg"]} for e in exc.errors()]
        err = ValidationError("Invalid request parameters", details={"errors": errs})
        return JSONResponse(status_code=422, content=err.to_dict())

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    return app


app = create_app() if os.environ.get("ADAPTER_TOKEN") else None
