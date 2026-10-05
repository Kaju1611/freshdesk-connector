import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from common import AuthError, ValidationError, normalize_domain
from app import verify as verify_mod
from app.config import Settings
from app.main import create_app

ADMIN = {"X-Admin-Token": "admin-secret"}
INTERNAL = {"X-Internal-Token": "internal-secret"}
BODY = {"domain": "https://Acme.freshdesk.com/", "api_key": "abcd1234efgh5678"}


def _settings(tmp_path):
    return Settings(master_keys=Fernet.generate_key().decode(), admin_token="admin-secret",
                    internal_token="internal-secret", db_path=str(tmp_path / "t.db"))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(verify_mod, "verify_freshdesk_key", lambda d, k, b=None: None)
    return TestClient(create_app(_settings(tmp_path)))


def test_register_and_read_back_roundtrip(client):
    r = client.put("/v1/tenants/t1/freshdesk", json=BODY, headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["domain"] == "acme"
    assert r.json()["key_last4"] == "5678"
    assert "abcd1234efgh5678" not in r.text
    c = client.get("/internal/v1/tenants/t1/credentials", headers=INTERNAL)
    assert c.json()["api_key"] == BODY["api_key"]


def test_key_is_encrypted_at_rest(client, tmp_path):
    client.put("/v1/tenants/t1/freshdesk", json=BODY, headers=ADMIN)
    assert BODY["api_key"].encode() not in (tmp_path / "t.db").read_bytes()


def test_token_separation(client):
    client.put("/v1/tenants/t1/freshdesk", json=BODY, headers=ADMIN)
    assert client.get("/internal/v1/tenants/t1/credentials", headers=ADMIN).status_code == 401
    assert client.put("/v1/tenants/t1/freshdesk", json=BODY, headers=INTERNAL).status_code == 401
    assert client.get("/v1/tenants/t1").status_code == 401


def test_missing_tenant_404(client):
    r = client.get("/internal/v1/tenants/nope/credentials", headers=INTERNAL)
    assert r.status_code == 404 and r.json()["error"]["code"] == "credentials_not_found"


def test_delete(client):
    client.put("/v1/tenants/t1/freshdesk", json=BODY, headers=ADMIN)
    assert client.delete("/v1/tenants/t1", headers=ADMIN).status_code == 204
    assert client.get("/v1/tenants/t1", headers=ADMIN).status_code == 404


def test_bad_tenant_id_rejected(client):
    r = client.put("/v1/tenants/bad.id/freshdesk", json=BODY, headers=ADMIN)
    assert r.status_code == 422


def test_failed_verification_blocks_storage(tmp_path, monkeypatch):
    def boom(d, k, b=None):
        raise AuthError("Freshdesk rejected the API key")
    monkeypatch.setattr(verify_mod, "verify_freshdesk_key", boom)
    c = TestClient(create_app(_settings(tmp_path)))
    assert c.put("/v1/tenants/t1/freshdesk", json=BODY, headers=ADMIN).status_code == 401
    assert c.get("/v1/tenants/t1", headers=ADMIN).status_code == 404


def test_key_rotation(tmp_path):
    from app.crypto import Crypto
    from app.store import CredentialStore
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    db = str(tmp_path / "r.db")
    CredentialStore(db, Crypto(old)).upsert("t1", "acme", "abcd1234efgh5678")
    assert CredentialStore(db, Crypto(f"{new},{old}")).rotate_all() == 1
    only_new = CredentialStore(db, Crypto(new))
    assert only_new.get_credentials("t1").api_key.get_secret_value() == "abcd1234efgh5678"


@pytest.mark.parametrize("bad", ["evil.com/x", "a b", "-x", "acme.freshdesk.com.evil.com", ""])
def test_domain_validation(bad):
    with pytest.raises(ValidationError):
        normalize_domain(bad)
