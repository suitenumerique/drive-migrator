"""Tests for ResanaTokenManager."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.utils import timezone

import jwt
import pytest
from cryptography.fernet import Fernet

from core.encryption import decrypt_token, encrypt_token
from core.factories import UserFactory
from core.sources.resana.token_manager import (
    ResanaTokenExpired,
    ResanaTokenManager,
    _token_expires_at,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(access="", refresh="", expires_at=None):
    return UserFactory(
        resana_access_token=encrypt_token(access) if access else "",
        resana_refresh_token=encrypt_token(refresh) if refresh else "",
        resana_token_expires_at=expires_at,
    )


def _make_access_jwt(exp_delta=timedelta(hours=3)):
    """Build a fake Resana access token JWT carrying an `exp` claim."""
    exp = timezone.now() + exp_delta
    return jwt.encode(
        {"exp": int(exp.timestamp())},
        "test-secret-long-enough-for-hs256",
        algorithm="HS256",
    )


# ---------------------------------------------------------------------------
# is_connected()
# ---------------------------------------------------------------------------


def test_is_connected_false_when_no_tokens():
    user = _make_user()
    assert ResanaTokenManager(user).is_connected() is False


def test_is_connected_false_when_only_refresh():
    user = _make_user(refresh="some-refresh")
    assert ResanaTokenManager(user).is_connected() is False


def test_is_connected_true_when_access_token_present():
    user = _make_user(access="some-access", refresh="some-refresh")
    assert ResanaTokenManager(user).is_connected() is True


def test_is_connected_true_even_when_token_expired():
    """is_connected() only checks presence, not validity."""
    user = _make_user(
        access="some-access",
        expires_at=timezone.now() - timedelta(hours=1),
    )
    assert ResanaTokenManager(user).is_connected() is True


# ---------------------------------------------------------------------------
# store_tokens()
# ---------------------------------------------------------------------------


def test_store_tokens_encrypts_and_persists(settings):
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()
    user = _make_user()
    access = _make_access_jwt()
    ResanaTokenManager(user).store_tokens(access, "plain-refresh")

    user.refresh_from_db()
    assert decrypt_token(user.resana_access_token) == access
    assert decrypt_token(user.resana_refresh_token) == "plain-refresh"
    assert user.resana_token_expires_at is not None


def test_store_tokens_sets_expiry_from_token_exp_claim(settings):
    """expires_at must come from the access token's own `exp` claim, not a hardcoded duration."""
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()
    user = _make_user()
    access = _make_access_jwt(timedelta(minutes=15))

    ResanaTokenManager(user).store_tokens(access, "plain-refresh")

    user.refresh_from_db()
    expected = timezone.now() + timedelta(minutes=15)
    assert abs((user.resana_token_expires_at - expected).total_seconds()) < 5


def test_store_tokens_does_not_store_plaintext(settings):
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()
    user = _make_user()
    access = _make_access_jwt()
    ResanaTokenManager(user).store_tokens(access, "plain-refresh")

    user.refresh_from_db()
    assert user.resana_access_token != access
    assert user.resana_refresh_token != "plain-refresh"


# ---------------------------------------------------------------------------
# _token_expires_at()
# ---------------------------------------------------------------------------


def test_token_expires_at_reads_exp_claim():
    """_token_expires_at derives expiry from the JWT's own exp claim."""
    access = _make_access_jwt(timedelta(minutes=42))
    expires_at = _token_expires_at(access)
    expected = timezone.now() + timedelta(minutes=42)
    assert abs((expires_at - expected).total_seconds()) < 5


# ---------------------------------------------------------------------------
# get_valid_token() — token still valid
# ---------------------------------------------------------------------------


def test_get_valid_token_returns_decrypted_access(settings):
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()
    user = _make_user(
        access="my-access",
        refresh="my-refresh",
        expires_at=timezone.now() + timedelta(hours=2),
    )
    token = ResanaTokenManager(user).get_valid_token()
    assert token == "my-access"


def test_get_valid_token_does_not_refresh_when_still_valid(settings):
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()
    user = _make_user(
        access="my-access",
        refresh="my-refresh",
        expires_at=timezone.now() + timedelta(hours=2),
    )
    with patch.object(ResanaTokenManager, "_refresh") as mock_refresh:
        ResanaTokenManager(user).get_valid_token()
    mock_refresh.assert_not_called()


# ---------------------------------------------------------------------------
# get_valid_token() — token expired, refresh succeeds
# ---------------------------------------------------------------------------


def test_get_valid_token_refreshes_when_expired(settings):
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()
    user = _make_user(
        access="old-access",
        refresh="my-refresh",
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    def fake_refresh(self):
        self.user.resana_access_token = encrypt_token("new-access")
        self.user.resana_token_expires_at = timezone.now() + timedelta(hours=3)
        self.user.save()

    with patch.object(ResanaTokenManager, "_refresh", fake_refresh):
        token = ResanaTokenManager(user).get_valid_token()

    assert token == "new-access"


def test_get_valid_token_refreshes_when_expires_at_is_none(settings):
    """No expiry stored → treat as expired and refresh."""
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()
    user = _make_user(access="old-access", refresh="my-refresh", expires_at=None)

    def fake_refresh(self):
        self.user.resana_access_token = encrypt_token("refreshed")
        self.user.resana_token_expires_at = timezone.now() + timedelta(hours=3)
        self.user.save()

    with patch.object(ResanaTokenManager, "_refresh", fake_refresh):
        token = ResanaTokenManager(user).get_valid_token()

    assert token == "refreshed"


# ---------------------------------------------------------------------------
# get_valid_token() — no refresh token available
# ---------------------------------------------------------------------------


def test_get_valid_token_raises_when_no_refresh_token(settings):
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()
    user = _make_user(
        access="old-access",
        expires_at=timezone.now() - timedelta(seconds=1),
    )
    with pytest.raises(ResanaTokenExpired):
        ResanaTokenManager(user).get_valid_token()


# ---------------------------------------------------------------------------
# _refresh() — HTTP interaction
# ---------------------------------------------------------------------------


def test_refresh_calls_authservice_endpoint(settings):
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()
    settings.RESANA_AUTHSERVICE_ENDPOINT = (
        "https://resana.example.com/auth-service/public/api"
    )
    user = _make_user(
        access="old-access",
        refresh="my-refresh",
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.cookies = {"interstis_access": _make_access_jwt()}

    with patch(
        "core.sources.resana.token_manager.requests.post", return_value=mock_response
    ) as mock_post:
        ResanaTokenManager(user)._refresh()  # pylint: disable=protected-access

    mock_post.assert_called_once_with(
        "https://resana.example.com/auth-service/public/api/public/token/access",
        headers={
            "Cookie": "interstis_refresh=my-refresh",
            "Accept": "application/json",
        },
        timeout=30,
    )
    mock_response.raise_for_status.assert_called_once()


def test_refresh_stores_new_access_token(settings):
    """The new access token comes from the interstis_access Set-Cookie, not the JSON body.

    Resana's AuthService response body only contains {"success": ..., "message": ...},
    the actual token is set via a Set-Cookie header.
    """
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()
    settings.RESANA_AUTHSERVICE_ENDPOINT = (
        "https://resana.example.com/auth-service/public/api"
    )
    user = _make_user(
        access="old-access",
        refresh="my-refresh",
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    new_access = _make_access_jwt(timedelta(minutes=15))
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "success": True,
        "message": "New Access Token created successfully",
    }
    mock_response.cookies = {"interstis_access": new_access}

    with patch(
        "core.sources.resana.token_manager.requests.post", return_value=mock_response
    ):
        ResanaTokenManager(user)._refresh()  # pylint: disable=protected-access

    user.refresh_from_db()
    assert decrypt_token(user.resana_access_token) == new_access
    expected_expiry = timezone.now() + timedelta(minutes=15)
    assert abs((user.resana_token_expires_at - expected_expiry).total_seconds()) < 5


def test_refresh_raises_token_expired_when_no_access_cookie(settings):
    """If the AuthService response doesn't set interstis_access, treat it as a failed refresh."""
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()
    settings.RESANA_AUTHSERVICE_ENDPOINT = (
        "https://resana.example.com/auth-service/public/api"
    )
    user = _make_user(
        access="old-access",
        refresh="my-refresh",
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.cookies = {}

    with patch(
        "core.sources.resana.token_manager.requests.post", return_value=mock_response
    ):
        with pytest.raises(ResanaTokenExpired):
            ResanaTokenManager(user)._refresh()  # pylint: disable=protected-access

    user.refresh_from_db()
    assert user.resana_access_token == ""
    assert user.resana_refresh_token == ""
    assert user.resana_token_expires_at is None


def test_refresh_raises_token_expired_on_401(settings):
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()
    settings.RESANA_AUTHSERVICE_ENDPOINT = (
        "https://resana.example.com/auth-service/public/api"
    )
    user = _make_user(
        access="old-access",
        refresh="expired-refresh",
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    mock_response = MagicMock()
    mock_response.status_code = 401

    with patch(
        "core.sources.resana.token_manager.requests.post", return_value=mock_response
    ):
        with pytest.raises(ResanaTokenExpired):
            ResanaTokenManager(user)._refresh()  # pylint: disable=protected-access

    user.refresh_from_db()
    assert user.resana_access_token == ""
    assert user.resana_refresh_token == ""
    assert user.resana_token_expires_at is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fernet_key() -> str:
    """Return a valid Fernet key for test settings."""
    return Fernet.generate_key().decode()
