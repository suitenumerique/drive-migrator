"""Tests for ResanaTokenManager."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.utils import timezone

import jwt
import pytest
import requests
from cryptography.fernet import Fernet

from core.encryption import decrypt_token, encrypt_token
from core.factories import UserFactory
from core.sources.resana.migrator_auth import MigratorClient
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
# store_tokens()
# ---------------------------------------------------------------------------


def test_store_tokens_encrypts_and_persists_all_fields(settings):
    """The offline token, bridge access token, session id and csrf token are all persisted."""
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()
    user = _make_user()
    access = _make_access_jwt()

    ResanaTokenManager(user).store_tokens(
        offline_token="off-tok",
        access_token=access,
        session_id="sess-id",
        csrf_token="csrf-value",
    )

    user.refresh_from_db()
    assert decrypt_token(user.resana_refresh_token) == "off-tok"
    assert decrypt_token(user.resana_access_token) == access
    assert decrypt_token(user.resana_session_id) == "sess-id"
    assert decrypt_token(user.resana_csrf_token) == "csrf-value"
    assert user.resana_token_expires_at is not None


def test_store_tokens_sets_expiry_from_access_token_exp_claim(settings):
    """expires_at must come from the bridge access token's own exp claim."""
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()
    user = _make_user()
    access = _make_access_jwt(timedelta(minutes=5))

    ResanaTokenManager(user).store_tokens(
        offline_token="off-tok",
        access_token=access,
        session_id="sess-id",
        csrf_token="csrf-value",
    )

    user.refresh_from_db()
    expected = timezone.now() + timedelta(minutes=5)
    assert abs((user.resana_token_expires_at - expected).total_seconds()) < 5


# ---------------------------------------------------------------------------
# clear_tokens()
# ---------------------------------------------------------------------------


def test_clear_tokens_clears_the_bridge_session_fields_too(settings):
    """clear_tokens() must wipe the session id and csrf token, not just the OIDC tokens."""
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()
    user = _make_user(access="acc", refresh="off")
    ResanaTokenManager(user).store_tokens(
        offline_token="off-tok",
        access_token=_make_access_jwt(),
        session_id="sess-id",
        csrf_token="csrf-value",
    )

    ResanaTokenManager(user).clear_tokens()

    user.refresh_from_db()
    assert user.resana_access_token == ""
    assert user.resana_refresh_token == ""
    assert user.resana_session_id == ""
    assert user.resana_csrf_token == ""
    assert user.resana_token_expires_at is None


# ---------------------------------------------------------------------------
# get_session_id() / get_csrf_token()
# ---------------------------------------------------------------------------


def test_get_session_id_returns_decrypted_value(settings):
    """The stored PHPSESSID must come back in plaintext for use in the Cookie header."""
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()
    user = _make_user()
    ResanaTokenManager(user).store_tokens(
        offline_token="off-tok",
        access_token=_make_access_jwt(),
        session_id="sess-id",
        csrf_token="csrf-value",
    )

    assert ResanaTokenManager(user).get_session_id() == "sess-id"


def test_get_csrf_token_returns_decrypted_value(settings):
    """The stored csrfToken must come back in plaintext for the X-CSRF-TOKEN header."""
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()
    user = _make_user()
    ResanaTokenManager(user).store_tokens(
        offline_token="off-tok",
        access_token=_make_access_jwt(),
        session_id="sess-id",
        csrf_token="csrf-value",
    )

    assert ResanaTokenManager(user).get_csrf_token() == "csrf-value"


# ---------------------------------------------------------------------------
# _refresh() — Keycloak offline token grant + bridge call
# ---------------------------------------------------------------------------


def _make_user_with_migrator_tokens(settings, session_id="old-sess", **overrides):
    """Build a user already connected through the new offline-token/bridge flow."""
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()
    user = _make_user()
    ResanaTokenManager(user).store_tokens(
        offline_token=overrides.get("offline_token", "old-off"),
        access_token=overrides.get(
            "access_token", _make_access_jwt(timedelta(seconds=-1))
        ),
        session_id=session_id,
        csrf_token=overrides.get("csrf_token", "old-csrf"),
    )
    user.resana_token_expires_at = timezone.now() - timedelta(seconds=1)
    user.save()
    return user


def test_refresh_renews_the_offline_token_against_keycloak(settings):
    """_refresh() must call Keycloak's refresh_token grant, not the old Auth Service."""
    settings.RESANA_KEYCLOAK_ENDPOINT = "https://kc.example.com/realms/ONHEXAGONE"
    settings.RESANA_MIGRATOR_CLIENT_ID = "resana-migrator"
    settings.RESANA_MIGRATOR_CLIENT_SECRET = "s3cr3t"
    settings.RESANA_MIGRATOR_REDIRECT_URI = "https://migrator.example.com/callback"
    settings.RESANA_WEB_ENDPOINT = "https://resana.example.com"
    user = _make_user_with_migrator_tokens(settings)

    with patch(
        "core.sources.resana.token_manager.refresh_offline_token"
    ) as mock_refresh, patch(
        "core.sources.resana.token_manager.create_bridge_session"
    ) as mock_bridge:
        mock_refresh.return_value = {
            "access_token": _make_access_jwt(),
            "refresh_token": "new-off",
        }
        mock_bridge.return_value = {
            "plateformeSessionId": "new-sess",
            "interstis_access": _make_access_jwt(),
            "csrfToken": "new-csrf",
        }
        ResanaTokenManager(user)._refresh()  # pylint: disable=protected-access

    mock_refresh.assert_called_once_with(
        "https://kc.example.com/realms/ONHEXAGONE",
        MigratorClient(
            client_id="resana-migrator",
            client_secret="s3cr3t",
            redirect_uri="https://migrator.example.com/callback",
        ),
        "old-off",
    )


def test_refresh_calls_the_bridge_with_the_new_access_token_and_existing_session(
    settings
):
    """The bridge call must reuse the previous PHPSESSID so it can replace that session."""
    settings.RESANA_KEYCLOAK_ENDPOINT = "https://kc.example.com/realms/ONHEXAGONE"
    settings.RESANA_MIGRATOR_CLIENT_ID = "resana-migrator"
    settings.RESANA_MIGRATOR_CLIENT_SECRET = "s3cr3t"
    settings.RESANA_WEB_ENDPOINT = "https://resana.example.com"
    user = _make_user_with_migrator_tokens(settings, session_id="old-sess")

    new_access = _make_access_jwt()
    with patch(
        "core.sources.resana.token_manager.refresh_offline_token"
    ) as mock_refresh, patch(
        "core.sources.resana.token_manager.create_bridge_session"
    ) as mock_bridge:
        mock_refresh.return_value = {
            "access_token": new_access,
            "refresh_token": "new-off",
        }
        mock_bridge.return_value = {
            "plateformeSessionId": "new-sess",
            "interstis_access": _make_access_jwt(),
            "csrfToken": "new-csrf",
        }
        ResanaTokenManager(user)._refresh()  # pylint: disable=protected-access

    mock_bridge.assert_called_once_with(
        "https://resana.example.com/public/auth/dinum-session",
        new_access,
        existing_session_id="old-sess",
    )


def test_refresh_persists_the_new_session_from_the_bridge(settings):
    """After a successful refresh, all four fields must reflect the bridge's fresh session."""
    settings.RESANA_KEYCLOAK_ENDPOINT = "https://kc.example.com/realms/ONHEXAGONE"
    settings.RESANA_MIGRATOR_CLIENT_ID = "resana-migrator"
    settings.RESANA_MIGRATOR_CLIENT_SECRET = "s3cr3t"
    settings.RESANA_WEB_ENDPOINT = "https://resana.example.com"
    user = _make_user_with_migrator_tokens(settings)

    new_bridge_access = _make_access_jwt(timedelta(minutes=5))
    with patch(
        "core.sources.resana.token_manager.refresh_offline_token"
    ) as mock_refresh, patch(
        "core.sources.resana.token_manager.create_bridge_session"
    ) as mock_bridge:
        mock_refresh.return_value = {
            "access_token": "kc-access",
            "refresh_token": "new-off",
        }
        mock_bridge.return_value = {
            "plateformeSessionId": "new-sess",
            "interstis_access": new_bridge_access,
            "csrfToken": "new-csrf",
        }
        ResanaTokenManager(user)._refresh()  # pylint: disable=protected-access

    user.refresh_from_db()
    assert decrypt_token(user.resana_refresh_token) == "new-off"
    assert decrypt_token(user.resana_access_token) == new_bridge_access
    assert decrypt_token(user.resana_session_id) == "new-sess"
    assert decrypt_token(user.resana_csrf_token) == "new-csrf"


def test_refresh_keeps_the_offline_token_when_keycloak_does_not_rotate_it(settings):
    """Keycloak may return the same offline token unchanged; _refresh() must not lose it."""
    settings.RESANA_KEYCLOAK_ENDPOINT = "https://kc.example.com/realms/ONHEXAGONE"
    settings.RESANA_MIGRATOR_CLIENT_ID = "resana-migrator"
    settings.RESANA_MIGRATOR_CLIENT_SECRET = "s3cr3t"
    settings.RESANA_WEB_ENDPOINT = "https://resana.example.com"
    user = _make_user_with_migrator_tokens(settings, offline_token="stable-off")

    with patch(
        "core.sources.resana.token_manager.refresh_offline_token"
    ) as mock_refresh, patch(
        "core.sources.resana.token_manager.create_bridge_session"
    ) as mock_bridge:
        mock_refresh.return_value = {
            "access_token": "kc-access"
        }  # no refresh_token key
        mock_bridge.return_value = {
            "plateformeSessionId": "new-sess",
            "interstis_access": _make_access_jwt(),
            "csrfToken": "new-csrf",
        }
        ResanaTokenManager(user)._refresh()  # pylint: disable=protected-access

    user.refresh_from_db()
    assert decrypt_token(user.resana_refresh_token) == "stable-off"


def test_refresh_raises_token_expired_and_clears_tokens_on_401(settings):
    """A revoked/expired offline token must clear all stored Resana state."""
    settings.RESANA_KEYCLOAK_ENDPOINT = "https://kc.example.com/realms/ONHEXAGONE"
    settings.RESANA_MIGRATOR_CLIENT_ID = "resana-migrator"
    settings.RESANA_MIGRATOR_CLIENT_SECRET = "s3cr3t"
    settings.RESANA_WEB_ENDPOINT = "https://resana.example.com"
    user = _make_user_with_migrator_tokens(settings)

    http_error = requests.HTTPError("401 Unauthorized")
    http_error.response = MagicMock(status_code=401)

    with patch(
        "core.sources.resana.token_manager.refresh_offline_token",
        side_effect=http_error,
    ):
        with pytest.raises(ResanaTokenExpired):
            ResanaTokenManager(user)._refresh()  # pylint: disable=protected-access

    user.refresh_from_db()
    assert user.resana_access_token == ""
    assert user.resana_refresh_token == ""
    assert user.resana_session_id == ""
    assert user.resana_csrf_token == ""
    assert user.resana_token_expires_at is None


def test_refresh_propagates_other_http_errors_without_clearing_tokens(settings):
    """A transient 5xx from Keycloak must not be treated as a definitive expiry."""
    settings.RESANA_KEYCLOAK_ENDPOINT = "https://kc.example.com/realms/ONHEXAGONE"
    settings.RESANA_MIGRATOR_CLIENT_ID = "resana-migrator"
    settings.RESANA_MIGRATOR_CLIENT_SECRET = "s3cr3t"
    settings.RESANA_WEB_ENDPOINT = "https://resana.example.com"
    user = _make_user_with_migrator_tokens(settings)

    http_error = requests.HTTPError("503 Service Unavailable")
    http_error.response = MagicMock(status_code=503)

    with patch(
        "core.sources.resana.token_manager.refresh_offline_token",
        side_effect=http_error,
    ):
        with pytest.raises(requests.HTTPError):
            ResanaTokenManager(user)._refresh()  # pylint: disable=protected-access

    user.refresh_from_db()
    assert decrypt_token(user.resana_refresh_token) == "old-off"


def test_refresh_raises_token_expired_when_no_access_token_returned(settings):
    """A Keycloak response without an access_token must be treated as a failed refresh."""
    settings.RESANA_KEYCLOAK_ENDPOINT = "https://kc.example.com/realms/ONHEXAGONE"
    settings.RESANA_MIGRATOR_CLIENT_ID = "resana-migrator"
    settings.RESANA_MIGRATOR_CLIENT_SECRET = "s3cr3t"
    settings.RESANA_WEB_ENDPOINT = "https://resana.example.com"
    user = _make_user_with_migrator_tokens(settings)

    with patch(
        "core.sources.resana.token_manager.refresh_offline_token"
    ) as mock_refresh:
        mock_refresh.return_value = {}
        with pytest.raises(ResanaTokenExpired):
            ResanaTokenManager(user)._refresh()  # pylint: disable=protected-access

    user.refresh_from_db()
    assert user.resana_access_token == ""
    assert user.resana_refresh_token == ""


def test_refresh_raises_token_expired_when_bridge_rejects_the_refreshed_session(
    settings,
):
    """A bridge HTTPError during refresh (e.g. bad audience) must not crash uncaught.

    Without this, a Celery task or SynchronizeAPIView would see a raw
    requests.HTTPError instead of the ResanaTokenExpired they know how to
    handle (redirecting the user to reconnect).
    """
    settings.RESANA_KEYCLOAK_ENDPOINT = "https://kc.example.com/realms/ONHEXAGONE"
    settings.RESANA_MIGRATOR_CLIENT_ID = "resana-migrator"
    settings.RESANA_MIGRATOR_CLIENT_SECRET = "s3cr3t"
    settings.RESANA_WEB_ENDPOINT = "https://resana.example.com"
    user = _make_user_with_migrator_tokens(settings)

    with patch(
        "core.sources.resana.token_manager.refresh_offline_token"
    ) as mock_refresh, patch(
        "core.sources.resana.token_manager.create_bridge_session",
        side_effect=requests.HTTPError("401 Unauthorized"),
    ):
        mock_refresh.return_value = {
            "access_token": "kc-access",
            "refresh_token": "new-off",
        }
        with pytest.raises(ResanaTokenExpired):
            ResanaTokenManager(user)._refresh()  # pylint: disable=protected-access

    user.refresh_from_db()
    assert user.resana_access_token == ""
    assert user.resana_refresh_token == ""
    assert user.resana_session_id == ""
    assert user.resana_csrf_token == ""
    assert user.resana_token_expires_at is None


def test_refresh_raises_token_expired_when_bridge_response_is_malformed(settings):
    """A malformed (but 200 OK) bridge response during refresh must not crash uncaught."""
    settings.RESANA_KEYCLOAK_ENDPOINT = "https://kc.example.com/realms/ONHEXAGONE"
    settings.RESANA_MIGRATOR_CLIENT_ID = "resana-migrator"
    settings.RESANA_MIGRATOR_CLIENT_SECRET = "s3cr3t"
    settings.RESANA_WEB_ENDPOINT = "https://resana.example.com"
    user = _make_user_with_migrator_tokens(settings)

    with patch(
        "core.sources.resana.token_manager.refresh_offline_token"
    ) as mock_refresh, patch(
        "core.sources.resana.token_manager.create_bridge_session",
        side_effect=ValueError("Bridge response missing fields: csrfToken"),
    ):
        mock_refresh.return_value = {
            "access_token": "kc-access",
            "refresh_token": "new-off",
        }
        with pytest.raises(ResanaTokenExpired):
            ResanaTokenManager(user)._refresh()  # pylint: disable=protected-access

    user.refresh_from_db()
    assert user.resana_access_token == ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fernet_key() -> str:
    """Return a valid Fernet key for test settings."""
    return Fernet.generate_key().decode()
