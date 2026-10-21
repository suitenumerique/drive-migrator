"""Tests for the Drive (ProConnect) token logs: state and refresh."""

# pylint: disable=protected-access

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import MagicMock, patch

from django.utils import timezone

import pytest
from cryptography.fernet import Fernet
from freezegun import freeze_time
from requests.exceptions import HTTPError, Timeout, TooManyRedirects

from core.destinations.drive.drive_backend import (
    DriveTokenExpired,
    DriveUserTokenBackend,
    describe_drive_tokens,
)
from core.encryption import encrypt_token
from core.tests.destinations.drive.drive_http_mock import patch_drive_http

TEST_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def set_encryption_key(settings):
    """Encrypt the test tokens with a throwaway key."""
    settings.OIDC_TOKENS_ENCRYPTION_KEY = TEST_KEY


@pytest.fixture()
def proconnect_settings(settings):
    """Point the refresh at a fake ProConnect token endpoint."""
    settings.OIDC_OP_TOKEN_ENDPOINT = "https://oidc.example.com/token"
    settings.OIDC_RP_CLIENT_ID = "osmose-client"
    settings.OIDC_RP_CLIENT_SECRET = "osmose-secret"


def _make_user(
    access_token="initial-tok", refresh_token="refresh-tok", expires_at=None
):
    """Build a mock user with tokens already encrypted, as stored in the real DB."""
    user = MagicMock()
    user.id = "user-1"
    user.oidc_access_token = encrypt_token(access_token) if access_token else ""
    user.oidc_refresh_token = encrypt_token(refresh_token) if refresh_token else ""
    user.oidc_token_expires_at = expires_at
    return user


def _rendered(log_method):
    """Return the messages logged through a mocked logger method, formatted."""
    return [call.args[0] % call.args[1:] for call in log_method.call_args_list]


# ---------------------------------------------------------------------------
# describe_drive_tokens()
# ---------------------------------------------------------------------------


@freeze_time("2026-10-19 15:35:37")
def test_describe_drive_tokens_with_both_tokens():
    """Both tokens are described, the access token with its expiry, never with their value."""
    user = _make_user(
        access_token="secret-access",
        refresh_token="secret-refresh",
        expires_at=datetime(2026, 10, 19, 16, 22, 0, tzinfo=dt_timezone.utc),
    )

    description = describe_drive_tokens(user)

    assert description == (
        "Drive (ProConnect) tokens for user user-1: "
        "access token present, expires at 2026-10-19T16:22:00+00:00 (in 2783s); "
        "refresh token present"
    )
    assert "secret" not in description


def test_describe_drive_tokens_without_tokens():
    """A user who never logged in through ProConnect has both tokens missing."""
    user = _make_user(access_token="", refresh_token="")

    assert describe_drive_tokens(user) == (
        "Drive (ProConnect) tokens for user user-1: "
        "access token missing; refresh token missing"
    )


# ---------------------------------------------------------------------------
# DriveUserTokenBackend._refresh() — logs
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("proconnect_settings")
@freeze_time("2026-10-19 15:35:37")
@pytest.mark.parametrize(
    "returned_refresh_token,rotation",
    [("new-refresh-tok", "rotated"), (None, "unchanged")],
)
def test_user_token_refresh_logs_the_expired_token_and_the_new_lifetime(
    returned_refresh_token, rotation
):
    """A successful refresh logs the old expiry, then the new one and the rotation."""
    user = _make_user(
        refresh_token="valid-refresh-tok",
        expires_at=datetime(2026, 10, 19, 15, 35, 0, tzinfo=dt_timezone.utc),
    )
    payload = {"access_token": "new-tok", "expires_in": 3600}
    if returned_refresh_token:
        payload["refresh_token"] = returned_refresh_token

    with (
        patch("core.destinations.drive.drive_backend.logger") as mock_logger,
        patch_drive_http() as http,
    ):
        http.post.return_value.json.return_value = payload
        DriveUserTokenBackend(user)._get_token()

    assert _rendered(mock_logger.info) == [
        "Drive (ProConnect) token refresh for user user-1: access token expired "
        "since 2026-10-19T15:35:00+00:00 (37s ago), refreshing with the refresh token",
        "Drive (ProConnect) token refresh succeeded for user user-1: new access "
        "token expires at 2026-10-19T16:35:37+00:00 (in 3600s), "
        f"refresh token {rotation}",
    ]
    mock_logger.warning.assert_not_called()


@pytest.mark.usefixtures("proconnect_settings")
def test_user_token_refresh_logs_the_proconnect_error():
    """A refused refresh token logs ProConnect's status and OAuth error."""
    user = _make_user(expires_at=timezone.now() - timedelta(seconds=1))
    response = MagicMock(status_code=400)
    response.json.return_value = {
        "error": "invalid_grant",
        "error_description": "grant request is invalid",
    }

    with (
        patch("core.destinations.drive.drive_backend.logger") as mock_logger,
        patch_drive_http() as http,
    ):
        http.post.return_value.raise_for_status.side_effect = HTTPError(
            "400 Bad Request", response=response
        )
        with pytest.raises(DriveTokenExpired):
            DriveUserTokenBackend(user)._get_token()

    assert _rendered(mock_logger.warning) == [
        "Drive (ProConnect) token refresh failed for user user-1: ProConnect "
        "returned 400 invalid_grant (grant request is invalid)"
    ]


def test_user_token_refresh_logs_a_missing_refresh_token():
    """An expired access token without refresh token is logged as a failed refresh."""
    user = _make_user(
        refresh_token="", expires_at=timezone.now() - timedelta(seconds=1)
    )

    with patch("core.destinations.drive.drive_backend.logger") as mock_logger:
        with pytest.raises(RuntimeError):
            DriveUserTokenBackend(user)._get_token()

    assert _rendered(mock_logger.warning) == [
        "Drive (ProConnect) token refresh failed for user user-1: "
        "no refresh token stored"
    ]


@pytest.fixture()
def no_retry_delay():
    """Skip tenacity's real sleeping between attempts."""
    with patch("tenacity.nap.time.sleep"):
        yield


def _server_error_response():
    """A ProConnect 503 response whose body isn't JSON."""
    response = MagicMock(status_code=503)
    response.json.side_effect = ValueError
    response.raise_for_status.side_effect = HTTPError(
        "503 Service Unavailable", response=response
    )
    return response


@pytest.mark.usefixtures("proconnect_settings", "no_retry_delay")
@pytest.mark.parametrize(
    "first_outcome",
    [_server_error_response(), Timeout("timed out")],
    ids=["503", "timeout"],
)
def test_user_token_refresh_retried_by_the_session_logs_no_failure(first_outcome):
    """A transient failure the session retries away doesn't log a failed refresh."""
    user = _make_user(expires_at=timezone.now() - timedelta(seconds=1))
    success = MagicMock()
    success.json.return_value = {"access_token": "new-tok", "expires_in": 60}

    with (
        patch("core.destinations.drive.drive_backend.logger") as mock_logger,
        patch_drive_http() as http,
    ):
        http.post.side_effect = [first_outcome, success]
        DriveUserTokenBackend(user)._get_token()

    mock_logger.warning.assert_not_called()
    assert "succeeded" in _rendered(mock_logger.info)[-1]


@pytest.mark.usefixtures("proconnect_settings", "no_retry_delay")
def test_user_token_refresh_logs_a_failure_once_retries_are_exhausted(settings):
    """Once the session gives up on a 5xx, the refresh failure is logged once."""
    settings.DRIVE_RETRY_MAX_ATTEMPTS = 2
    user = _make_user(expires_at=timezone.now() - timedelta(seconds=1))

    with (
        patch("core.destinations.drive.drive_backend.logger") as mock_logger,
        patch_drive_http() as http,
    ):
        http.post.side_effect = [_server_error_response(), _server_error_response()]
        with pytest.raises(HTTPError):
            DriveUserTokenBackend(user)._get_token()

    assert http.post.call_count == 2
    assert _rendered(mock_logger.warning) == [
        "Drive (ProConnect) token refresh failed for user user-1: "
        "ProConnect returned 503 without OAuth error"
    ]


def test_describe_drive_tokens_access_token_without_expiry_will_be_refreshed():
    """Without expiry date the access token is refreshed, not trusted."""
    user = _make_user(expires_at=None)

    description = describe_drive_tokens(user)

    assert "access token present, expiry unknown (will be refreshed);" in description


@pytest.mark.usefixtures("proconnect_settings")
def test_user_token_refresh_logs_a_missing_access_token():
    """The refresh start log reports a missing access token, not an expiry."""
    user = _make_user(access_token="", expires_at=None)

    with (
        patch("core.destinations.drive.drive_backend.logger") as mock_logger,
        patch_drive_http() as http,
    ):
        http.post.return_value.json.return_value = {
            "access_token": "new-tok",
            "expires_in": 60,
        }
        DriveUserTokenBackend(user)._get_token()

    assert _rendered(mock_logger.info)[0] == (
        "Drive (ProConnect) token refresh for user user-1: access token missing, "
        "refreshing with the refresh token"
    )


@pytest.mark.usefixtures("proconnect_settings")
def test_user_token_refresh_logs_a_new_access_token_without_expiry():
    """A new access token without expires_in reads as refreshed again, not valid forever."""
    user = _make_user(expires_at=timezone.now() - timedelta(seconds=1))

    with (
        patch("core.destinations.drive.drive_backend.logger") as mock_logger,
        patch_drive_http() as http,
    ):
        http.post.return_value.json.return_value = {"access_token": "new-tok"}
        DriveUserTokenBackend(user)._get_token()

    assert _rendered(mock_logger.info)[-1] == (
        "Drive (ProConnect) token refresh succeeded for user user-1: new access "
        "token expiry unknown (will be refreshed), refresh token unchanged"
    )


@pytest.mark.usefixtures("proconnect_settings")
@pytest.mark.parametrize(
    "json_outcome,expected_error",
    [({"expires_in": 60}, KeyError), (ValueError("not JSON"), ValueError)],
)
def test_user_token_refresh_logs_a_response_without_access_token(
    json_outcome, expected_error
):
    """A successful answer without usable access token is logged as a failure."""
    user = _make_user(expires_at=timezone.now() - timedelta(seconds=1))

    with (
        patch("core.destinations.drive.drive_backend.logger") as mock_logger,
        patch_drive_http() as http,
    ):
        http.post.return_value.json.side_effect = [json_outcome]
        with pytest.raises(expected_error):
            DriveUserTokenBackend(user)._get_token()

    assert _rendered(mock_logger.warning) == [
        "Drive (ProConnect) token refresh failed for user user-1: ProConnect "
        f"returned no access token ({expected_error.__name__})"
    ]


@pytest.mark.usefixtures("proconnect_settings")
def test_user_token_refresh_logs_another_request_error():
    """A request error the session doesn't retry is logged as a failed refresh."""
    user = _make_user(expires_at=timezone.now() - timedelta(seconds=1))

    with (
        patch("core.destinations.drive.drive_backend.logger") as mock_logger,
        patch_drive_http() as http,
    ):
        http.post.side_effect = TooManyRedirects("too many")
        with pytest.raises(TooManyRedirects):
            DriveUserTokenBackend(user)._get_token()

    assert _rendered(mock_logger.warning) == [
        "Drive (ProConnect) token refresh failed for user user-1: ProConnect "
        "request failed (TooManyRedirects)"
    ]
