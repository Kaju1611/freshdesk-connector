.PHONY: venv gen-key mint-token spec test-cred test-adapter test-mcp test-e2e test up

VENV := .venv
PY   := $(abspath $(VENV))/bin/python

# One virtualenv for all services (works on "externally managed" system Pythons too).
$(VENV)/.ready: libs/common/pyproject.toml
	python -m venv $(VENV)
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -e libs/common fastapi uvicorn httpx cryptography pydantic-settings redis fakeredis pytest "mcp>=2.3,<3"
	touch $@
venv: $(VENV)/.ready

gen-key: venv
	@$(PY) -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
mint-token:
	@python3 scripts/mint_tenant_token.py $(TENANT)
spec: venv
	$(PY) scripts/export_tool_spec.py

test-cred: venv
	cd services/credential-service && $(PY) -m pytest -q
test-adapter: venv
	cd services/freshdesk-adapter && $(PY) -m pytest -q
test-mcp: venv
	cd services/mcp-server && $(PY) -m pytest -q
test-e2e: venv
	cd tests/integration && $(PY) -m pytest -q
test: test-cred test-adapter test-mcp test-e2e

up:
	docker compose up --build
