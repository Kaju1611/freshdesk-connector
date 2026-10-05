"""Typed errors shared by every service.

Each error carries a stable machine-readable `code`, an HTTP status, and a
`retryable` flag so the MCP layer can tell the agent whether retrying makes sense.
"""
from __future__ import annotations
from typing import Any


class ConnectorError(Exception):
    code = "internal_error"
    status_code = 500
    retryable = False

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "details": self.details,
            }
        }


class ValidationError(ConnectorError):
    code, status_code = "validation_error", 422


class AuthError(ConnectorError):
    code, status_code = "auth_error", 401


class CredentialNotFoundError(ConnectorError):
    code, status_code = "credentials_not_found", 404


class NotFoundError(ConnectorError):
    code, status_code = "not_found", 404


class RateLimitedError(ConnectorError):
    code, status_code, retryable = "rate_limited", 429, True

    def __init__(self, message: str, *, retry_after: float | None = None):
        super().__init__(message, details={"retry_after_seconds": retry_after})
        self.retry_after = retry_after


class UpstreamError(ConnectorError):
    code, status_code, retryable = "upstream_error", 502, True


class UpstreamAuthError(ConnectorError):
    """Freshdesk rejected the stored API key (revoked/rotated/insufficient rights)."""
    code, status_code = "upstream_auth_error", 502
