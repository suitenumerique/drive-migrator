"""Symmetric encryption helpers for sensitive fields stored in the database."""

from django.conf import settings

from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    key = settings.OIDC_TOKENS_ENCRYPTION_KEY
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(value: str) -> str:
    """Return the Fernet-encrypted ciphertext of value, or '' if value is empty."""
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def decrypt_token(value: str) -> str:
    """Return the plaintext of a Fernet-encrypted value, or '' if value is empty."""
    if not value:
        return ""
    return _fernet().decrypt(value.encode()).decode()
