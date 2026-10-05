from cryptography.fernet import Fernet, MultiFernet


class Crypto:
    """Fernet (AES-128-CBC + HMAC) with key rotation via MultiFernet."""

    def __init__(self, keys_csv: str):
        keys = [k.strip() for k in keys_csv.split(",") if k.strip()]
        if not keys:
            raise ValueError("MASTER_KEYS must contain at least one Fernet key")
        self._f = MultiFernet([Fernet(k) for k in keys])

    def encrypt(self, plaintext: str) -> bytes:
        return self._f.encrypt(plaintext.encode())

    def decrypt(self, token: bytes) -> str:
        return self._f.decrypt(token).decode()

    def rotate(self, token: bytes) -> bytes:
        """Re-encrypt an existing token under the primary (first) key."""
        return self._f.rotate(token)
