"""Tests for DriveUserExchangedTokenBackend: Menshen token exchange (RFC 8693)."""

# pylint: disable=protected-access

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import MagicMock, patch

from django.utils import timezone

import httpx2
import pytest
from cryptography.fernet import Fernet
from freezegun import freeze_time
from menshen_client import TokenExchangeRequest, TokenType
from menshen_client.exceptions import ResponseParsingError
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import HTTPError

from core import factories
from core.destinations.drive import drive_backend
from core.destinations.drive.drive_backend import (
    DriveAuthMode,
    DriveTokenExpired,
    DriveUserExchangedTokenBackend,
    DriveUserTokenBackend,
    clear_drive_tokens,
    describe_drive_tokens,
    get_drive_auth_mode,
    make_user_drive_backend,
    uses_user_identity,
)
from core.encryption import decrypt_token, encrypt_token
from core.tests.destinations.drive.drive_http_mock import patch_drive_http

pytestmark = pytest.mark.django_db

TEST_KEY = Fernet.generate_key().decode()
NOW = datetime(2026, 11, 5, 10, 0, 0, tzinfo=dt_timezone.utc)


@pytest.fixture(autouse=True)
def exchange_settings(settings):
    """Encrypt the test tokens with a throwaway key and configure Menshen."""
    settings.OIDC_TOKENS_ENCRYPTION_KEY = TEST_KEY
    settings.DRIVE_AUTH_MODE = "user_exchanged_token"
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"
    settings.OIDC_OP_TOKEN_ENDPOINT = "https://oidc.example.com/token"
    settings.OIDC_RP_CLIENT_ID = "osmose-client"
    settings.OIDC_RP_CLIENT_SECRET = "osmose-secret"
    settings.MENSHEN_SERVER_ROOT_URL = "https://menshen.example.com"
    settings.MENSHEN_CLIENT_ID = "migrator"
    settings.MENSHEN_CLIENT_SECRET = "migrator-secret"
    settings.MENSHEN_REQUESTS_TIMEOUT = 12.0
    settings.DRIVE_TOKEN_EXCHANGE_AUDIENCE = "drive"
    settings.DRIVE_TOKEN_EXCHANGE_SCOPE = "openid email"


@pytest.fixture(name="menshen")
def fixture_menshen():
    """Replace the Menshen client; exchanges return a 3-day token by default."""
    client = MagicMock()
    client.exchange.return_value = MagicMock(
        access_token="exchanged-tok", expires_in=3 * 24 * 3600
    )
    with patch.object(drive_backend, "_get_menshen_client", return_value=client):
        yield client


def _make_user(
    access_token="proconnect-tok",
    refresh_token="refresh-tok",
    expires_at=NOW + timedelta(minutes=30),
    exchanged_token="",
    exchanged_expires_at=None,
):
    """Create a user with tokens encrypted as stored in the real DB."""
    return factories.UserFactory(
        oidc_access_token=encrypt_token(access_token) if access_token else "",
        oidc_refresh_token=encrypt_token(refresh_token) if refresh_token else "",
        oidc_token_expires_at=expires_at,
        drive_exchanged_token=encrypt_token(exchanged_token) if exchanged_token else "",
        drive_exchanged_token_expires_at=exchanged_expires_at,
    )


def _menshen_error(status_code, payload=None):
    request = httpx2.Request("POST", "https://menshen.example.com/auth/token/exchange/")
    response = httpx2.Response(status_code, json=payload or {}, request=request)
    return httpx2.HTTPStatusError("error", request=request, response=response)


# ---------------------------------------------------------------------------
# Auth mode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode, backend_class, user_identity",
    [
        ("user_exchanged_token", DriveUserExchangedTokenBackend, True),
        ("user_token", DriveUserTokenBackend, True),
    ],
)
def test_make_user_drive_backend_follows_the_auth_mode(
    settings, mode, backend_class, user_identity
):
    """Each user mode builds its own backend, acting as the user."""
    settings.DRIVE_AUTH_MODE = mode

    assert isinstance(make_user_drive_backend(_make_user()), backend_class)
    assert uses_user_identity() is user_identity


def test_invalid_auth_mode_falls_back_to_the_service_account(settings):
    """A mistyped mode must not act as the user."""
    settings.DRIVE_AUTH_MODE = "user-exchanged-token"

    assert get_drive_auth_mode() is DriveAuthMode.SERVICE_ACCOUNT
    assert uses_user_identity() is False


def test_menshen_client_is_configured_from_the_settings():
    """The client authenticates to the configured Menshen server."""
    drive_backend._get_menshen_client.cache_clear()
    try:
        config = drive_backend._get_menshen_client().config
    finally:
        drive_backend._get_menshen_client.cache_clear()

    assert config.client_id == "migrator"
    assert config.client_secret == "migrator-secret"
    assert config.token_url == "https://menshen.example.com/auth/token/exchange/"
    assert config.requests_timeout == 12.0


# ---------------------------------------------------------------------------
# exchange()
# ---------------------------------------------------------------------------


@freeze_time(NOW)
def test_exchange_stores_the_exchanged_token(menshen):
    """The ProConnect token is exchanged for Drive, and the result stored apart."""
    user = _make_user()

    DriveUserExchangedTokenBackend(user).exchange()

    menshen.exchange.assert_called_once_with(
        TokenExchangeRequest(
            subject_token="proconnect-tok",
            subject_token_type=TokenType.ACCESS_TOKEN,
            audience="drive",
            scope="openid email",
        )
    )
    user.refresh_from_db()
    assert decrypt_token(user.drive_exchanged_token) == "exchanged-tok"
    assert user.drive_exchanged_token_expires_at == NOW + timedelta(days=3)
    assert decrypt_token(user.oidc_access_token) == "proconnect-tok"


@freeze_time(NOW)
def test_exchange_refreshes_an_expired_proconnect_token_first(menshen):
    """An expired subject token is refreshed, and stored, before the exchange."""
    user = _make_user(expires_at=NOW - timedelta(seconds=1))

    with patch_drive_http() as http:
        http.post.return_value.json.return_value = {
            "access_token": "new-proconnect-tok",
            "expires_in": 3600,
        }
        DriveUserExchangedTokenBackend(user).exchange()

    assert http.post.call_args.args[0] == "https://oidc.example.com/token"
    request = menshen.exchange.call_args.args[0]
    assert request.subject_token == "new-proconnect-tok"
    user.refresh_from_db()
    assert decrypt_token(user.oidc_access_token) == "new-proconnect-tok"
    assert decrypt_token(user.drive_exchanged_token) == "exchanged-tok"


@pytest.mark.parametrize(
    "error, expected",
    [
        (_menshen_error(400, {"error": "invalid_grant"}), DriveTokenExpired),
        (_menshen_error(401, {"error": "invalid_client"}), HTTPError),
        (_menshen_error(503), RequestsConnectionError),
        (httpx2.ConnectError("refused"), RequestsConnectionError),
        (ResponseParsingError("Invalid token exchange response"), ResponseParsingError),
    ],
)
@freeze_time(NOW)
def test_exchange_failures_are_mapped_and_store_nothing(menshen, error, expected):
    """A refused ProConnect token asks for a new login; other failures are
    raised as the request errors callers already handle."""
    menshen.exchange.side_effect = error
    user = _make_user()

    with (
        patch.object(drive_backend, "logger") as mock_logger,
        pytest.raises(expected),
    ):
        DriveUserExchangedTokenBackend(user).exchange()

    user.refresh_from_db()
    assert user.drive_exchanged_token == ""
    assert user.drive_exchanged_token_expires_at is None
    assert mock_logger.warning.call_args.args[:3] == (
        "Menshen token exchange for audience %r failed for user %s: %s",
        "drive",
        user.id,
    )


def test_exchange_with_an_unrefreshable_proconnect_token_asks_for_a_login(menshen):
    """Without a usable ProConnect token there is nothing to exchange."""
    user = _make_user(
        refresh_token="", expires_at=timezone.now() - timedelta(seconds=1)
    )

    with pytest.raises(DriveTokenExpired):
        DriveUserExchangedTokenBackend(user).exchange()

    menshen.exchange.assert_not_called()


# ---------------------------------------------------------------------------
# Using the stored token
# ---------------------------------------------------------------------------


@freeze_time(NOW)
def test_stored_exchanged_token_is_sent_to_drive_without_exchanging(menshen):
    """A Celery task uses the token exchanged at launch, even once the
    ProConnect token has expired."""
    user = _make_user(
        expires_at=NOW - timedelta(hours=1),
        exchanged_token="stored-exchanged-tok",
        exchanged_expires_at=NOW + timedelta(days=2),
    )

    with patch_drive_http() as http:
        http.post.return_value.json.return_value = {"id": "root-uuid"}
        DriveUserExchangedTokenBackend(user).create_folder("Workspace")

    menshen.exchange.assert_not_called()
    assert (
        http.post.call_args.kwargs["headers"]["Authorization"]
        == "Bearer stored-exchanged-tok"
    )


@freeze_time(NOW)
def test_expired_exchanged_token_is_exchanged_again(menshen):
    """Past its expiry, the token is exchanged again while ProConnect allows it."""
    user = _make_user(
        exchanged_token="old-exchanged-tok",
        exchanged_expires_at=NOW - timedelta(seconds=1),
    )

    token = DriveUserExchangedTokenBackend(user)._get_token()

    assert token == "exchanged-tok"
    menshen.exchange.assert_called_once()
    user.refresh_from_db()
    assert decrypt_token(user.drive_exchanged_token) == "exchanged-tok"


def test_login_leaves_the_exchanged_token_alone():
    """Each login overwrites the ProConnect tokens, never the exchanged one."""
    # pylint: disable-next=import-outside-toplevel
    from core.authentication.backends import OIDCAuthenticationBackend

    user = _make_user(
        exchanged_token="stored-exchanged-tok",
        exchanged_expires_at=timezone.now() + timedelta(days=2),
    )
    backend = OIDCAuthenticationBackend()
    backend._token_info = {"refresh_token": "new-refresh", "expires_in": 60}

    backend._persist_oidc_tokens(user, "new-proconnect-tok")

    user.refresh_from_db()
    assert decrypt_token(user.oidc_access_token) == "new-proconnect-tok"
    assert decrypt_token(user.drive_exchanged_token) == "stored-exchanged-tok"


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


@freeze_time(NOW)
def test_describe_drive_tokens_in_exchange_mode():
    """The logs show the exchanged token and the ProConnect tokens it relies on."""
    user = _make_user(
        exchanged_token="stored-exchanged-tok",
        exchanged_expires_at=NOW + timedelta(days=2),
    )

    assert describe_drive_tokens(user) == (
        f"Drive (Menshen) tokens for user {user.id}: exchanged token present, "
        "expires at 2026-11-07T10:00:00+00:00 (in 172800s); ProConnect access "
        "token present, expires at 2026-11-05T10:30:00+00:00 (in 1800s); "
        "ProConnect refresh token present"
    )


def test_clear_drive_tokens_clears_the_exchanged_token():
    """Resetting the Drive connection also drops the exchanged token."""
    user = _make_user(
        exchanged_token="stored-exchanged-tok",
        exchanged_expires_at=timezone.now() + timedelta(days=2),
    )

    clear_drive_tokens(user)

    user.refresh_from_db()
    assert user.drive_exchanged_token == ""
    assert user.drive_exchanged_token_expires_at is None
    assert user.oidc_access_token == ""
