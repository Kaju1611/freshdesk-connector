"""Boots the real stack as separate OS processes:
stub-freshdesk, credential-service, freshdesk-adapter, mcp-server  (each from its own directory,
so each imports its own `app` package exactly as in production)."""
import hashlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2]
ADMIN, INTERNAL, ADAPTER = "e2e-admin", "e2e-internal", "e2e-adapter"

# tenant -> (bearer token, freshdesk domain)
TENANTS = {
    "acme_ws": ("tok-acme", "acme"),
    "globex_ws": ("tok-globex", "globex"),
    "rl_ws": ("tok-rl", "ratelimited"),
    "shed_ws": ("tok-shed", "shedding"),
    "rot_ws": ("tok-rot", "rotating"),
    "flaky_ws": ("tok-flaky", "flaky"),
    "ghost_ws": ("tok-ghost", "ghost"),       # valid token, never registered
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_ready(url: str, proc: subprocess.Popen, name: str, logfile: Path):
    for _ in range(100):
        if proc.poll() is not None:
            pytest.fail(f"{name} exited early:\n{logfile.read_text()[-2000:]}")
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.1)
    pytest.fail(f"{name} did not become ready:\n{logfile.read_text()[-2000:]}")


class Stack:
    def __init__(self):
        self.stub = self.cred = self.adapter = self.mcp = ""
        self.logs: dict[str, Path] = {}
        self.procs: list[subprocess.Popen] = []

    # helpers used by tests
    def seed(self, domain, key, tickets):
        httpx.post(f"{self.stub}/__admin/domains/{domain}", json={"key": key, "tickets": tickets}).raise_for_status()

    def register(self, tenant, domain, key):
        return httpx.put(f"{self.cred}/v1/tenants/{tenant}/freshdesk", headers={"X-Admin-Token": ADMIN},
                         json={"domain": domain, "api_key": key})

    def stub_log(self, domain):
        return httpx.get(f"{self.stub}/__admin/log", params={"domain": domain}).json()

    def fail(self, domain, *responses):
        httpx.post(f"{self.stub}/__admin/domains/{domain}/fail", json={"responses": list(responses)}).raise_for_status()


@pytest.fixture(scope="session")
def stack(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e")
    st = Stack()
    ports = {n: free_port() for n in ("stub", "cred", "adapter", "mcp")}
    st.stub, st.cred = f"http://127.0.0.1:{ports['stub']}", f"http://127.0.0.1:{ports['cred']}"
    st.adapter, st.mcp = f"http://127.0.0.1:{ports['adapter']}", f"http://127.0.0.1:{ports['mcp']}"
    template = f"{st.stub}/{{domain}}"
    base_env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    for k in list(base_env):
        if k in ("MASTER_KEYS", "ADAPTER_TOKEN", "TENANT_TOKENS", "REDIS_URL"):
            del base_env[k]

    tenant_tokens = ",".join(f"{hashlib.sha256(t.encode()).hexdigest()}:{ten}" for ten, (t, _) in TENANTS.items())
    specs = {
        "stub": (ROOT / "tests/integration", "stub_freshdesk:app", ports["stub"], {}),
        "cred": (ROOT / "services/credential-service", "app.main:app", ports["cred"], {
            "MASTER_KEYS": Fernet.generate_key().decode(), "ADMIN_TOKEN": ADMIN, "INTERNAL_TOKEN": INTERNAL,
            "DB_PATH": str(tmp / "cred.db"), "FRESHDESK_BASE_URL_TEMPLATE": template}),
        "adapter": (ROOT / "services/freshdesk-adapter", "app.main:app", ports["adapter"], {
            "CREDENTIAL_SERVICE_URL": st.cred, "INTERNAL_TOKEN": INTERNAL, "ADAPTER_TOKEN": ADAPTER,
            "FRESHDESK_BASE_URL_TEMPLATE": template, "CACHE_TTL_SECONDS": "0", "BACKOFF_BASE": "0.05",
            "MAX_WAIT_SECONDS": "3", "TENANT_RPM": "1000"}),
        "mcp": (ROOT / "services/mcp-server", "app.main:app", ports["mcp"], {
            "ADAPTER_URL": st.adapter, "ADAPTER_TOKEN": ADAPTER, "TENANT_TOKENS": tenant_tokens}),
    }
    try:
        for name, (cwd, target, port, env) in specs.items():
            log = tmp / f"{name}.log"
            st.logs[name] = log
            p = subprocess.Popen([sys.executable, "-m", "uvicorn", target, "--host", "127.0.0.1",
                                  "--port", str(port), "--log-level", "warning"],
                                 cwd=cwd, env={**base_env, **env}, stdout=open(log, "w"), stderr=subprocess.STDOUT)
            st.procs.append(p)
            wait_ready(f"http://127.0.0.1:{port}/healthz", p, name, log)
        yield st
    finally:
        for p in st.procs:
            p.terminate()
        for p in st.procs:
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
