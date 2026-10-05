from __future__ import annotations
import hmac
import logging
import os

from fastapi import Depends, FastAPI, Header, Path, Request
from fastapi.responses import JSONResponse

from common import AuthError, ConnectorError, CredentialNotFoundError, TenantInfo, TenantRegistration
from common.logging import configure_logging

from . import verify as verify_mod
from .config import Settings
from .crypto import Crypto
from .store import CredentialStore

TenantId = Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")
log = logging.getLogger("credential-service")


def _check(provided: str | None, expected: str) -> None:
    if not provided or not hmac.compare_digest(provided, expected):
        raise AuthError("Missing or invalid service token")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]
    configure_logging("credential-service", settings.log_level)
    store = CredentialStore(settings.db_path, Crypto(settings.master_keys))
    app = FastAPI(title="credential-service", version="0.1.0")

    def admin(x_admin_token: str | None = Header(default=None)) -> None:
        _check(x_admin_token, settings.admin_token)

    def internal(x_internal_token: str | None = Header(default=None)) -> None:
        _check(x_internal_token, settings.internal_token)

    @app.exception_handler(ConnectorError)
    async def _handler(_: Request, exc: ConnectorError):
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    # --- onboarding (admin token) ---
    @app.put("/v1/tenants/{tenant_id}/freshdesk", response_model=TenantInfo,
             dependencies=[Depends(admin)])
    def register(body: TenantRegistration, tenant_id: str = TenantId):
        key = body.api_key.get_secret_value()
        if settings.verify_on_register:
            verify_mod.verify_freshdesk_key(body.domain, key, settings.freshdesk_base_url_template)
        info = store.upsert(tenant_id, body.domain, key)
        log.info("tenant registered", extra={"ctx": {"tenant_id": tenant_id, "domain": body.domain}})
        return info

    @app.get("/v1/tenants/{tenant_id}", response_model=TenantInfo, dependencies=[Depends(admin)])
    def info(tenant_id: str = TenantId):
        i = store.get_info(tenant_id)
        if not i:
            raise CredentialNotFoundError(f"No credentials for tenant '{tenant_id}'")
        return i

    @app.delete("/v1/tenants/{tenant_id}", status_code=204, dependencies=[Depends(admin)])
    def remove(tenant_id: str = TenantId):
        if not store.delete(tenant_id):
            raise CredentialNotFoundError(f"No credentials for tenant '{tenant_id}'")

    # --- internal (adapter only, separate token) ---
    @app.get("/internal/v1/tenants/{tenant_id}/credentials", dependencies=[Depends(internal)])
    def credentials(tenant_id: str = TenantId):
        c = store.get_credentials(tenant_id)
        if not c:
            raise CredentialNotFoundError(f"No credentials for tenant '{tenant_id}'")
        # Explicit unwrap: the only place a secret leaves this service.
        return {"tenant_id": c.tenant_id, "domain": c.domain,
                "api_key": c.api_key.get_secret_value()}

    return app


# `uvicorn app.main:app` entrypoint; skipped when env isn't configured (e.g. under pytest).
app = create_app() if os.environ.get("MASTER_KEYS") else None
