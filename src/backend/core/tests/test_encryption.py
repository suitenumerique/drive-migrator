"""Unit tests for core.encryption helpers."""

import pytest
from cryptography.fernet import Fernet

from core.encryption import decrypt_token, encrypt_token

TEST_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def set_encryption_key(settings):
    settings.OIDC_TOKENS_ENCRYPTION_KEY = TEST_KEY


def test_encrypt_returns_string_different_from_input():
    """encrypt_token() returns a ciphertext string, not the plaintext."""
    result = encrypt_token("my-secret-token")
    assert isinstance(result, str)
    assert result != "my-secret-token"


def test_decrypt_reverses_encrypt():
    """decrypt_token(encrypt_token(x)) == x."""
    plaintext = "my-secret-token"
    assert decrypt_token(encrypt_token(plaintext)) == plaintext


def test_encrypt_empty_string_returns_empty():
    """encrypt_token('') returns '' without attempting Fernet encryption."""
    assert encrypt_token("") == ""


def test_decrypt_empty_string_returns_empty():
    """decrypt_token('') returns '' without attempting Fernet decryption."""
    assert decrypt_token("") == ""


def test_two_encryptions_of_same_value_differ():
    """Fernet uses a random IV — each encryption produces a unique ciphertext."""
    value = "my-secret-token"
    assert encrypt_token(value) != encrypt_token(value)


def test_decrypt_still_returns_original_despite_different_ciphertexts():
    """Despite unique ciphertexts, both decrypt to the same plaintext."""
    value = "my-secret-token"
    enc1, enc2 = encrypt_token(value), encrypt_token(value)
    assert decrypt_token(enc1) == decrypt_token(enc2) == value
