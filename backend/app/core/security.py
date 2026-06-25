"""
Encryption helpers for Shopify tokens + Clerk JWT verification.
"""

import base64
from functools import lru_cache
from cryptography.fernet import Fernet

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_fernet() -> Fernet:
    """Return a Fernet instance using the app encryption key."""
    settings = get_settings()
    key = settings.encryption_key
    if not key:
        raise ValueError("ENCRYPTION_KEY not set — cannot encrypt/decrypt tokens")
    # Ensure key is valid Fernet key (url-safe base64, 32 bytes)
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(plaintext: str) -> str:
    """Encrypt a Shopify access token."""
    f = get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a Shopify access token."""
    f = get_fernet()
    return f.decrypt(ciphertext.encode()).decode()
