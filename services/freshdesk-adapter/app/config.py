from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    credential_service_url: str = "http://credential-service:8001"
    internal_token: str            # adapter -> credential-service
    adapter_token: str             # mcp-server -> adapter
    redis_url: str = ""            # empty => in-memory limiter/cache (single replica / dev only)

    # Rate limiting. Freshdesk limits are per account and plan-dependent; stay below yours.
    tenant_rpm: int = 100
    max_wait_seconds: float = 8.0  # longest we'll sleep inside a request; beyond this we shed load
    max_retries: int = 3
    backoff_base: float = 0.5
    backoff_cap: float = 8.0
    request_timeout: float = 15.0

    cache_ttl_seconds: int = 30    # 0 disables caching
    creds_ttl_seconds: int = 60
    include_private_notes: bool = False
    # Test/staging hook only. {domain} is a validated DNS label. Leave default in production.
    freshdesk_base_url_template: str = "https://{domain}.freshdesk.com"
    log_level: str = "INFO"
