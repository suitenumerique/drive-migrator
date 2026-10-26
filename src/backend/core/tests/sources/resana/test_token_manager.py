"""Tests for ResanaTokenManager."""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import MagicMock, patch

from django.utils import timezone

import jwt
import pytest
import requests
from cryptography.fernet import Fernet
from freezegun import freeze_time

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


def test_get_valid_token_clears_tokens_when_no_refresh_token(settings):
    """A token that cannot be renewed is cleared, so the user shows as disconnected."""
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()
    user = _make_user(
        access="old-access",
        expires_at=timezone.now() - timedelta(seconds=1),
    )
    with pytest.raises(ResanaTokenExpired):
        ResanaTokenManager(user).get_valid_token()

    user.refresh_from_db()
    assert not user.resana_access_token
    assert ResanaTokenManager(user).is_connected() is False


@pytest.mark.parametrize("fresh", [True, False])
def test_get_valid_token_clears_unreadable_tokens(settings, fresh):
    """Tokens encrypted with another key are cleared and raise ResanaTokenExpired."""
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()
    other_key = Fernet(_fernet_key().encode())
    user = UserFactory(
        resana_access_token=other_key.encrypt(b"old-access").decode(),
        resana_refresh_token=other_key.encrypt(b"old-offline").decode(),
        resana_token_expires_at=timezone.now() + timedelta(hours=1 if fresh else -1),
    )
    with pytest.raises(ResanaTokenExpired):
        ResanaTokenManager(user).get_valid_token()

    user.refresh_from_db()
    assert not user.resana_access_token
    assert not user.resana_refresh_token


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

    previous_updated_at = user.updated_at
    ResanaTokenManager(user).clear_tokens()

    user.refresh_from_db()
    assert user.resana_access_token == ""
    assert user.resana_refresh_token == ""
    assert user.resana_session_id == ""
    assert user.resana_csrf_token == ""
    assert user.resana_token_expires_at is None
    assert user.updated_at > previous_updated_at


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
# get_valid_token() — concurrent refreshes for the same user
# ---------------------------------------------------------------------------


def test_concurrent_refresh_loser_must_not_clobber_the_winners_tokens(settings):
    """Two workers refreshing the same user's expired token (e.g. two workspace
    exports) each hold their own in-memory User. Worker A refreshes first and
    Keycloak rotates the offline token; worker B then submits the now-stale
    offline token, gets a 400, and must NOT wipe the fresh tokens A just stored.
    """
    settings.RESANA_KEYCLOAK_ENDPOINT = "https://kc.example.com/realms/ONHEXAGONE"
    settings.RESANA_MIGRATOR_CLIENT_ID = "resana-migrator"
    settings.RESANA_MIGRATOR_CLIENT_SECRET = "s3cr3t"
    settings.RESANA_WEB_ENDPOINT = "https://resana.example.com"
    user_a = _make_user_with_migrator_tokens(settings)
    user_b = type(user_a).objects.get(pk=user_a.pk)  # stale copy, as another worker
    new_access = _make_access_jwt()

    rotated = requests.HTTPError("400 Bad Request")
    rotated.response = MagicMock(status_code=400)

    with patch(
        "core.sources.resana.token_manager.refresh_offline_token",
        side_effect=[
            {"access_token": _make_access_jwt(), "refresh_token": "new-off"},
            rotated,
        ],
    ), patch("core.sources.resana.token_manager.create_bridge_session") as mock_bridge:
        mock_bridge.return_value = {
            "plateformeSessionId": "new-sess",
            "interstis_access": new_access,
            "csrfToken": "new-csrf",
        }
        assert ResanaTokenManager(user_a).get_valid_token() == new_access
        assert ResanaTokenManager(user_b).get_valid_token() == new_access

    user_a.refresh_from_db()
    assert decrypt_token(user_a.resana_refresh_token) == "new-off"
    assert decrypt_token(user_a.resana_session_id) == "new-sess"
    assert ResanaTokenManager(user_a).is_connected()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fernet_key() -> str:
    """Return a valid Fernet key for test settings."""
    return Fernet.generate_key().decode()


# ---------------------------------------------------------------------------
# describe_tokens()
# ---------------------------------------------------------------------------


def _make_offline_jwt(claims):
    """Build a fake Keycloak offline token carrying the given claims."""
    return jwt.encode(claims, "test-secret-long-enough-for-hs256", algorithm="HS256")


@freeze_time("2026-10-15 15:18:08")
def test_describe_tokens_with_both_tokens():
    """Both tokens are described with their expiry, never with their value."""
    offline = _make_offline_jwt({"typ": "Offline", "exp": 1792681088})
    user = _make_user(
        access="secret-access",
        refresh=offline,
        expires_at=datetime(2026, 10, 15, 15, 20, 0, tzinfo=dt_timezone.utc),
    )

    description = ResanaTokenManager(user).describe_tokens()

    assert description == (
        f"Resana tokens for user {user.id}: "
        "access token present, expires at 2026-10-15T15:20:00+00:00 (in 112s); "
        "offline token present, expires at 2026-10-22T14:58:08+00:00 (in 603600s)"
    )
    assert "secret-access" not in description
    assert offline not in description


def test_describe_tokens_offline_token_without_exp():
    """A Keycloak offline token usually carries no exp claim."""
    user = _make_user(refresh=_make_offline_jwt({"typ": "Offline"}))

    description = ResanaTokenManager(user).describe_tokens()

    assert description.endswith("offline token present, no expiry")


def test_describe_tokens_offline_token_not_a_jwt():
    """An offline token that can't be decoded doesn't break the description."""
    user = _make_user(refresh="not-a-jwt")

    description = ResanaTokenManager(user).describe_tokens()

    assert description.endswith("offline token present, expiry unreadable")


def test_describe_tokens_without_tokens():
    """A disconnected user has both tokens missing."""
    user = _make_user()

    assert ResanaTokenManager(user).describe_tokens() == (
        f"Resana tokens for user {user.id}: "
        "access token missing; offline token missing"
    )


# ---------------------------------------------------------------------------
# _refresh() — logs
# ---------------------------------------------------------------------------


def _rendered(log_method):
    """Return the messages logged through a mocked logger method, formatted."""
    return [call.args[0] % call.args[1:] for call in log_method.call_args_list]


def _refresh_with(
    user, keycloak_response=None, keycloak_error=None, bridge=None, *, raises=None
):
    """Run _refresh() against mocked Keycloak and bridge, checking it raises the
    expected exception if any, and return the mocked logger."""
    if bridge is None:
        bridge = {
            "plateformeSessionId": "new-sess",
            "interstis_access": _make_access_jwt(),
            "csrfToken": "new-csrf",
        }
    with (
        patch("core.sources.resana.token_manager.logger") as mock_logger,
        patch(
            "core.sources.resana.token_manager.refresh_offline_token",
            return_value=keycloak_response,
            side_effect=keycloak_error,
        ),
        patch(
            "core.sources.resana.token_manager.create_bridge_session",
            **(
                {"side_effect": bridge}
                if isinstance(bridge, Exception)
                else {"return_value": bridge}
            ),
        ),
    ):
        if raises is None:
            ResanaTokenManager(user)._refresh()  # pylint: disable=protected-access
        else:
            with pytest.raises(raises):
                ResanaTokenManager(user)._refresh()  # pylint: disable=protected-access
    return mock_logger


@freeze_time("2026-10-15 15:18:08")
def test_refresh_logs_the_expired_token_and_the_new_lifetime(settings):
    """A successful refresh logs the old expiry, then the new one and the rotation."""
    user = _make_user_with_migrator_tokens(settings)

    mock_logger = _refresh_with(
        user, keycloak_response={"access_token": "kc", "refresh_token": "new-off"}
    )

    assert _rendered(mock_logger.info) == [
        f"Resana token refresh for user {user.id}: access token expired since "
        "2026-10-15T15:18:07+00:00 (1s ago), refreshing with the offline token",
        f"Resana token refresh succeeded for user {user.id}: new access token "
        "expires at 2026-10-15T18:18:08+00:00 (in 10800s), offline token rotated",
    ]
    mock_logger.warning.assert_not_called()


@freeze_time("2026-10-15 15:18:08")
def test_refresh_logs_an_offline_token_kept_by_keycloak(settings):
    """When Keycloak doesn't rotate the offline token, the log says so."""
    user = _make_user_with_migrator_tokens(settings)

    mock_logger = _refresh_with(user, keycloak_response={"access_token": "kc"})

    assert _rendered(mock_logger.info)[-1].endswith("offline token unchanged")


def test_refresh_logs_the_keycloak_error_when_the_offline_token_is_refused(settings):
    """A refused offline token logs Keycloak's status and OAuth error."""
    user = _make_user_with_migrator_tokens(settings)
    http_error = requests.HTTPError("400 Bad Request")
    http_error.response = MagicMock(status_code=400)
    http_error.response.json.return_value = {
        "error": "invalid_grant",
        "error_description": "Offline session not active",
    }

    mock_logger = _refresh_with(
        user, keycloak_error=http_error, raises=ResanaTokenExpired
    )

    assert _rendered(mock_logger.warning) == [
        f"Resana token refresh failed for user {user.id}: Keycloak returned 400 "
        "invalid_grant (Offline session not active)"
    ]


def test_refresh_logs_a_keycloak_error_without_json_body(settings):
    """A Keycloak error page that isn't JSON still logs its status."""
    user = _make_user_with_migrator_tokens(settings)
    http_error = requests.HTTPError("503 Service Unavailable")
    http_error.response = MagicMock(status_code=503)
    http_error.response.json.side_effect = ValueError

    mock_logger = _refresh_with(
        user, keycloak_error=http_error, raises=requests.HTTPError
    )

    assert _rendered(mock_logger.warning) == [
        f"Resana token refresh failed for user {user.id}: Keycloak returned 503 "
        "without OAuth error"
    ]


def test_refresh_logs_a_keycloak_response_without_access_token(settings):
    """A Keycloak answer without access token is logged as a failed refresh."""
    user = _make_user_with_migrator_tokens(settings)

    mock_logger = _refresh_with(user, keycloak_response={}, raises=ResanaTokenExpired)

    assert _rendered(mock_logger.warning) == [
        f"Resana token refresh failed for user {user.id}: Keycloak returned no "
        "access token"
    ]


def test_refresh_logs_the_bridge_rejecting_the_new_session(settings):
    """A bridge refusing the refreshed access token is logged with its error."""
    user = _make_user_with_migrator_tokens(settings)

    mock_logger = _refresh_with(
        user,
        keycloak_response={"access_token": "kc"},
        bridge=requests.HTTPError(
            "401 Unauthorized", response=MagicMock(status_code=401)
        ),
        raises=ResanaTokenExpired,
    )

    assert _rendered(mock_logger.warning) == [
        f"Resana token refresh failed for user {user.id}: the Resana bridge "
        "rejected the new session (HTTPError 401)"
    ]


def test_refresh_still_expires_tokens_when_keycloak_error_body_is_not_an_object(
    settings,
):
    """Logging an odd Keycloak error body never changes the refresh outcome."""
    user = _make_user_with_migrator_tokens(settings)
    http_error = requests.HTTPError("400 Bad Request")
    http_error.response = MagicMock(status_code=400)
    http_error.response.json.return_value = ["not", "an", "object"]

    with patch(
        "core.sources.resana.token_manager.refresh_offline_token",
        side_effect=http_error,
    ):
        with pytest.raises(ResanaTokenExpired):
            ResanaTokenManager(user)._refresh()  # pylint: disable=protected-access

    user.refresh_from_db()
    assert user.resana_refresh_token == ""


@pytest.mark.parametrize("exp", ["soon", 10**20])
def test_describe_tokens_offline_token_with_invalid_exp(exp):
    """An exp claim that isn't a usable timestamp doesn't break the description."""
    user = _make_user(refresh=_make_offline_jwt({"typ": "Offline", "exp": exp}))

    description = ResanaTokenManager(user).describe_tokens()

    assert description.endswith("offline token present, expiry unreadable")


def test_refresh_logs_a_failed_keycloak_request(settings):
    """A network error towards Keycloak is logged before being raised."""
    user = _make_user_with_migrator_tokens(settings)

    mock_logger = _refresh_with(
        user, keycloak_error=requests.Timeout("timed out"), raises=requests.Timeout
    )

    assert _rendered(mock_logger.warning) == [
        f"Resana token refresh failed for user {user.id}: Keycloak request "
        "failed (Timeout)"
    ]


def test_refresh_logs_a_failed_bridge_request(settings):
    """A network error towards the Resana bridge is logged before being raised."""
    user = _make_user_with_migrator_tokens(settings)

    mock_logger = _refresh_with(
        user,
        keycloak_response={"access_token": "kc"},
        bridge=requests.ConnectionError("refused"),
        raises=requests.ConnectionError,
    )

    assert _rendered(mock_logger.warning) == [
        f"Resana token refresh failed for user {user.id}: the Resana bridge "
        "request failed (ConnectionError)"
    ]


def test_describe_tokens_access_token_without_expiry_will_be_refreshed():
    """Without expiry date the access token is refreshed, not trusted."""
    user = _make_user(access="some-access", expires_at=None)

    description = ResanaTokenManager(user).describe_tokens()

    assert "access token present, expiry unknown (will be refreshed);" in description


def test_refresh_logs_an_access_token_without_expiry(settings):
    """The refresh start log doesn't present a dateless access token as valid."""
    user = _make_user_with_migrator_tokens(settings)
    user.resana_token_expires_at = None

    mock_logger = _refresh_with(user, keycloak_response={"access_token": "kc"})

    assert _rendered(mock_logger.info)[0] == (
        f"Resana token refresh for user {user.id}: access token expiry unknown "
        "(will be refreshed), refreshing with the offline token"
    )
