"""Tests for DriveMenshenTokenBackend and is_user_token_auth_mode()."""

# pylint: disable=protected-access

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.utils import timezone

import menshen_client
import pytest
from cryptography.fernet import Fernet
from menshen_client.exceptions import ResponseParsingError
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import HTTPError, Timeout

from core.destinations.drive.drive_backend import (
    DriveMenshenTokenBackend,
    DriveUserTokenBackend,
    is_user_token_auth_mode,
)
from core.encryption import decrypt_token, encrypt_token
from core.tests.destinations.drive.drive_http_mock import patch_drive_http

TEST_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def set_encryption_key(settings):
    settings.OIDC_TOKENS_ENCRYPTION_KEY = TEST_KEY


@pytest.fixture(autouse=True)
def no_retry_delay():
    """Skip real sleeping (tenacity's) so retry tests run instantly."""
    with patch("tenacity.nap.time.sleep"):
        yield


@pytest.fixture(autouse=True)
def menshen_settings(settings):
    """Menshen configuration for every test, overridable per-test."""
    settings.MENSHEN_SERVER_ROOT_URL = "https://menshen.example.com"
    settings.MENSHEN_CLIENT_ID = "menshen-client"
    settings.MENSHEN_CLIENT_SECRET = "menshen-secret"
    settings.MENSHEN_AUDIENCE = "https://drive.example.com"
    settings.MENSHEN_SCOPE = "drive:item:write"


def patched_exchange_client():
    """Replace the _exchange_client property: the mock is what the property
    returns, so mock.exchange(...) is the exchange call."""
    return patch(
        "core.destinations.drive.drive_backend.DriveMenshenTokenBackend._exchange_client"
    )


def _make_user(
    access_token="initial-tok",
    refresh_token="refresh-tok",
    expires_at=None,
    email="user@example.com",
):
    """Build a mock user with tokens already encrypted, as stored in the real DB."""
    user = MagicMock()
    user.email = email
    user.oidc_access_token = encrypt_token(access_token) if access_token else ""
    user.oidc_refresh_token = encrypt_token(refresh_token) if refresh_token else ""
    user.oidc_token_expires_at = expires_at
    return user


def _exchange_response(
    access_token="exchanged-tok",
    expires_in=300,
    scope="drive:item:write",
):
    """Build a TokenExchangeResponse-like object as returned by Menshen."""
    response = MagicMock()
    response.access_token = access_token
    response.expires_in = expires_in
    response.scope = scope
    return response


def _token_response(payload):
    """A ProConnect token endpoint response with an access token."""
    response = MagicMock(status_code=200)
    response.json.return_value = payload
    return response


def _oauth_error(status_code, error="invalid_request"):
    """Build the HTTPError Menshen returns on a rejected exchange."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = {"error": error}
    error = HTTPError(f"{status_code} Client Error")
    error.response = response
    return error


# ---------------------------------------------------------------------------
# is_user_token_auth_mode()
# ---------------------------------------------------------------------------


def test_is_user_token_auth_mode_default_is_false(settings):
    """service_account mode (the default) is not a user-token mode."""
    settings.DRIVE_AUTH_MODE = "service_account"
    assert is_user_token_auth_mode() is False


def test_is_user_token_auth_mode_true_for_user_token(settings):
    settings.DRIVE_AUTH_MODE = "user_token"
    assert is_user_token_auth_mode() is True


def test_is_user_token_auth_mode_true_for_menshen(settings):
    settings.DRIVE_AUTH_MODE = "menshen"
    assert is_user_token_auth_mode() is True


# ---------------------------------------------------------------------------
# DriveMenshenTokenBackend — token exchange
# ---------------------------------------------------------------------------


def test_menshen_get_token_exchanges_subject_token():
    """_get_token() exchanges the user's valid ProConnect token and returns
    the exchanged token, without calling the ProConnect token endpoint."""
    user = _make_user(
        access_token="stored-tok",
        expires_at=timezone.now() + timedelta(hours=1),
    )

    with (
        patch_drive_http() as mock_requests,
        patched_exchange_client() as mock_client,
    ):
        mock_client.exchange.return_value = _exchange_response()
        token = DriveMenshenTokenBackend(user)._get_token()

    mock_requests.post.assert_not_called()
    mock_client.exchange.assert_called_once()
    request = mock_client.exchange.call_args.args[0]
    assert request.subject_token == "stored-tok"
    assert request.subject_token_type == "urn:ietf:params:oauth:token-type:access_token"
    assert request.audience == "https://drive.example.com"
    assert request.scope == "drive:item:write"
    assert token == "exchanged-tok"


def test_menshen_exchange_client_built_from_settings():
    """The Menshen client is configured from the MENSHEN_* settings."""
    user = _make_user(
        access_token="stored-tok",
        expires_at=timezone.now() + timedelta(hours=1),
    )

    with patch.object(menshen_client, "TokenExchangeClient") as mock_client_cls:
        mock_client_cls.return_value.exchange.return_value = _exchange_response()
        DriveMenshenTokenBackend(user)._get_token()

    config = mock_client_cls.call_args.kwargs["config"]
    assert config.client_id == "menshen-client"
    assert config.client_secret == "menshen-secret"
    assert config.server_root_url == "https://menshen.example.com"


def test_menshen_get_token_without_scope_omits_scope(settings):
    """An empty MENSHEN_SCOPE is sent as no scope rather than an empty string."""
    settings.MENSHEN_SCOPE = ""
    user = _make_user(
        access_token="stored-tok",
        expires_at=timezone.now() + timedelta(hours=1),
    )

    with patched_exchange_client() as mock_client:
        mock_client.exchange.return_value = _exchange_response()
        DriveMenshenTokenBackend(user)._get_token()

    request = mock_client.exchange.call_args.args[0]
    assert request.scope is None


def test_menshen_get_token_exchanges_expired_subject_token(settings):
    """The subject token is refreshed via ProConnect first when expired, then
    exchanged for a Drive-scoped token."""
    settings.OIDC_OP_TOKEN_ENDPOINT = "https://oidc.example.com/token"
    settings.OIDC_RP_CLIENT_ID = "osmose-client"
    settings.OIDC_RP_CLIENT_SECRET = "osmose-secret"

    user = _make_user(
        access_token="expired-tok",
        refresh_token="valid-refresh-tok",
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    with (
        patch_drive_http() as mock_requests,
        patched_exchange_client() as mock_client,
    ):
        mock_requests.post.return_value = _token_response(
            {"access_token": "new-proconnect-tok", "expires_in": 60}
        )
        mock_client.exchange.return_value = _exchange_response()
        token = DriveMenshenTokenBackend(user)._get_token()

    mock_requests.post.assert_called_once()
    request = mock_client.exchange.call_args.args[0]
    assert request.subject_token == "new-proconnect-tok"
    assert token == "exchanged-tok"


def test_menshen_get_token_reuses_exchanged_token_when_valid():
    """The exchanged token is reused while valid: no second exchange call."""
    user = _make_user(
        access_token="stored-tok",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    backend = DriveMenshenTokenBackend(user)

    with patched_exchange_client() as mock_client:
        mock_client.exchange.return_value = _exchange_response()
        first = backend._get_token()
        second = backend._get_token()

    assert first == second == "exchanged-tok"
    assert mock_client.exchange.call_count == 1


def test_menshen_get_token_reexchanges_near_expiry():
    """The exchanged token is re-exchanged when near its expiry."""
    user = _make_user(
        access_token="stored-tok",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    backend = DriveMenshenTokenBackend(user)

    with patched_exchange_client() as mock_client:
        mock_client.exchange.return_value = _exchange_response(expires_in=5)
        backend._get_token()
        mock_client.exchange.return_value = _exchange_response(
            access_token="fresh-exchanged-tok", expires_in=300
        )
        second = backend._get_token()

    assert mock_client.exchange.call_count == 2
    assert second == "fresh-exchanged-tok"


def test_menshen_get_token_raises_when_no_refresh_token():
    """A missing ProConnect token and refresh token fails the exchange."""
    user = _make_user(
        access_token="expired-tok",
        refresh_token="",
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    with pytest.raises(RuntimeError, match="No refresh token stored"):
        DriveMenshenTokenBackend(user)._get_token()


def test_menshen_exchange_retries_on_timeout_then_succeeds():
    """A transient Timeout on the exchange call is retried and succeeds."""
    user = _make_user(
        access_token="stored-tok",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    backend = DriveMenshenTokenBackend(user)

    with patched_exchange_client() as mock_client:
        mock_client.exchange.side_effect = [
            Timeout("timed out"),
            _exchange_response(),
        ]
        token = backend._get_token()

    assert token == "exchanged-tok"
    assert mock_client.exchange.call_count == 2


def test_menshen_exchange_retries_on_connection_error_then_succeeds():
    """A transient ConnectionError on the exchange call is retried."""
    user = _make_user(
        access_token="stored-tok",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    backend = DriveMenshenTokenBackend(user)

    with patched_exchange_client() as mock_client:
        mock_client.exchange.side_effect = [
            RequestsConnectionError("connection refused"),
            _exchange_response(),
        ]
        token = backend._get_token()

    assert token == "exchanged-tok"


def test_menshen_exchange_does_not_retry_on_4xx():
    """A 4xx from Menshen (rejected exchange) fails fast: no retry."""
    user = _make_user(
        access_token="stored-tok",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    backend = DriveMenshenTokenBackend(user)

    with patched_exchange_client() as mock_client:
        mock_client.exchange.side_effect = _oauth_error(400)
        with pytest.raises(HTTPError):
            backend._get_token()

    assert mock_client.exchange.call_count == 1


def test_menshen_exchange_retries_on_5xx():
    """A Menshen-side 5xx is treated as transient and retried."""
    user = _make_user(
        access_token="stored-tok",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    backend = DriveMenshenTokenBackend(user)

    with patched_exchange_client() as mock_client:
        mock_client.exchange.side_effect = [
            _oauth_error(502),
            _exchange_response(),
        ]
        token = backend._get_token()

    assert token == "exchanged-tok"
    assert mock_client.exchange.call_count == 2


def test_menshen_exchange_does_not_retry_on_unparsable_response():
    """An unparsable Menshen response fails fast: no retry."""
    user = _make_user(
        access_token="stored-tok",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    backend = DriveMenshenTokenBackend(user)

    with patched_exchange_client() as mock_client:
        mock_client.exchange.side_effect = ResponseParsingError(
            "Invalid token exchange response"
        )
        with pytest.raises(ResponseParsingError):
            backend._get_token()

    assert mock_client.exchange.call_count == 1


def test_menshen_exchange_does_not_persist_exchanged_token(settings):
    """The exchanged token stays in memory: only the refreshed subject tokens
    are persisted to the user, unchanged from DriveUserTokenBackend."""
    settings.OIDC_OP_TOKEN_ENDPOINT = "https://oidc.example.com/token"
    settings.OIDC_RP_CLIENT_ID = "osmose-client"
    settings.OIDC_RP_CLIENT_SECRET = "osmose-secret"

    user = _make_user(
        access_token="expired-tok",
        refresh_token="valid-refresh-tok",
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    with (
        patch_drive_http() as mock_requests,
        patched_exchange_client() as mock_client,
    ):
        mock_requests.post.return_value = _token_response(
            {"access_token": "new-proconnect-tok", "expires_in": 60}
        )
        mock_client.exchange.return_value = _exchange_response(
            access_token="exchanged-tok"
        )
        DriveMenshenTokenBackend(user)._get_token()

    # The exchanged token was never written to the user model.
    assert decrypt_token(user.oidc_access_token) == "new-proconnect-tok"
    user.save.assert_called_once()


def test_menshen_backend_inherits_user_token_refresh_persistence(settings):
    """Subject token refresh and persistence behave exactly as in
    DriveUserTokenBackend."""
    settings.OIDC_OP_TOKEN_ENDPOINT = "https://oidc.example.com/token"
    settings.OIDC_RP_CLIENT_ID = "osmose-client"
    settings.OIDC_RP_CLIENT_SECRET = "osmose-secret"

    user = _make_user(
        access_token="expired-tok",
        refresh_token="old-refresh",
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    with (
        patch_drive_http() as mock_requests,
        patched_exchange_client() as mock_client,
    ):
        mock_requests.post.return_value = _token_response(
            {
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 60,
            }
        )
        mock_client.exchange.return_value = _exchange_response()
        DriveMenshenTokenBackend(user)._get_token()

    user.save.assert_called_once_with(
        update_fields=[
            "oidc_access_token",
            "oidc_refresh_token",
            "oidc_token_expires_at",
            "updated_at",
        ]
    )


def test_menshen_auth_uses_exchanged_token():
    """The auth callable sends the exchanged token as Bearer on Drive API calls,
    refreshed at each attempt preparation."""
    user = _make_user(
        access_token="stored-tok",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    backend = DriveMenshenTokenBackend(user)

    with patched_exchange_client() as mock_client:
        mock_client.exchange.return_value = _exchange_response(
            access_token="exchanged-tok"
        )
        prepared = SimpleNamespace(headers={})
        backend._auth(prepared)

    assert prepared.headers == {"Authorization": "Bearer exchanged-tok"}


def test_menshen_backend_is_a_user_token_backend():
    """DriveMenshenTokenBackend inherits DriveUserTokenBackend: same ProConnect
    seeding, refresh and persistence behavior."""
    assert issubclass(DriveMenshenTokenBackend, DriveUserTokenBackend)
