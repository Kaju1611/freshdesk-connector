from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Comma-separated Fernet keys. FIRST encrypts; ALL can decrypt (enables rotation).
    master_keys: str
    # Used by an operator/onboarding flow to register or delete tenants.
    admin_token: str
    # Used only by freshdesk-adapter to read decrypted credentials.
    internal_token: str
    db_path: str = "/data/credentials.db"
    verify_on_register: bool = True
    freshdesk_base_url_template: str = "https://{domain}.freshdesk.com"   # test/staging hook only
    log_level: str = "INFO"
