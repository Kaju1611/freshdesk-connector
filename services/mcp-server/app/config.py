from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    adapter_url: str = "http://freshdesk-adapter:8002"
    adapter_token: str
    # "<sha256-of-bearer-token>:<tenant_id>" pairs, comma-separated. Mint with scripts/mint_tenant_token.py.
    # The tenant is derived from the caller's token and NEVER from tool arguments.
    tenant_tokens: str
    # e.g. "mcp.example.com,mcp.example.com:*". Empty => Host-header check off (bearer auth still enforced).
    allowed_hosts: str = ""
    adapter_timeout: float = 30.0
    max_text_chars: int = 1500        # ticket description clip
    max_note_chars: int = 1000        # per-conversation clip
    log_level: str = "INFO"
