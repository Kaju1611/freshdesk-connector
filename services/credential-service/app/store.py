"""SQLite-backed encrypted credential store.

The interface is deliberately tiny so it can be swapped for Postgres / a cloud
secret manager when you run multiple replicas (SQLite is single-node).
"""
from __future__ import annotations
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from pydantic import SecretStr

from common import TenantCredentials, TenantInfo
from .crypto import Crypto

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
  tenant_id   TEXT PRIMARY KEY,
  domain      TEXT NOT NULL,
  api_key_enc BLOB NOT NULL,
  key_last4   TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
)
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CredentialStore:
    def __init__(self, path: str, crypto: Crypto):
        self.path, self.crypto = path, crypto
        with closing(self._conn()) as c, c:
            c.execute(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def upsert(self, tenant_id: str, domain: str, api_key: str) -> TenantInfo:
        now = _now()
        enc = self.crypto.encrypt(api_key)
        with closing(self._conn()) as c, c:
            c.execute(
                """INSERT INTO tenants VALUES (?,?,?,?,?,?)
                   ON CONFLICT(tenant_id) DO UPDATE SET
                     domain=excluded.domain, api_key_enc=excluded.api_key_enc,
                     key_last4=excluded.key_last4, updated_at=excluded.updated_at""",
                (tenant_id, domain, enc, api_key[-4:], now, now),
            )
        return self.get_info(tenant_id)  # type: ignore[return-value]

    def get_info(self, tenant_id: str) -> TenantInfo | None:
        with closing(self._conn()) as c:
            r = c.execute("SELECT * FROM tenants WHERE tenant_id=?", (tenant_id,)).fetchone()
        if not r:
            return None
        return TenantInfo(tenant_id=r["tenant_id"], domain=r["domain"], key_last4=r["key_last4"],
                          created_at=r["created_at"], updated_at=r["updated_at"])

    def get_credentials(self, tenant_id: str) -> TenantCredentials | None:
        with closing(self._conn()) as c:
            r = c.execute("SELECT * FROM tenants WHERE tenant_id=?", (tenant_id,)).fetchone()
        if not r:
            return None
        return TenantCredentials(tenant_id=tenant_id, domain=r["domain"],
                                 api_key=SecretStr(self.crypto.decrypt(r["api_key_enc"])))

    def delete(self, tenant_id: str) -> bool:
        with closing(self._conn()) as c, c:
            return c.execute("DELETE FROM tenants WHERE tenant_id=?", (tenant_id,)).rowcount > 0

    def rotate_all(self) -> int:
        """Re-encrypt every row under the primary key (run after adding a new key)."""
        n = 0
        with closing(self._conn()) as c, c:
            for r in c.execute("SELECT tenant_id, api_key_enc FROM tenants").fetchall():
                c.execute("UPDATE tenants SET api_key_enc=? WHERE tenant_id=?",
                          (self.crypto.rotate(r["api_key_enc"]), r["tenant_id"]))
                n += 1
        return n
