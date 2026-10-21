"""Tests for DriveBackend.find_user_by_email(), used to share migrated workspaces."""

# pylint: disable=protected-access

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.utils import timezone

import pytest
from requests.exceptions import HTTPError, Timeout

from core.destinations.drive.drive_backend import (
    DriveServiceAccountBackend,
    DriveTokenExpired,
    DriveUserTokenBackend,
)
from core.encryption import encrypt_token
from core.tests.destinations.drive.drive_http_mock import patch_drive_http


@pytest.fixture(autouse=True)
def no_retry_delay():
    """Skip real sleeping so retry tests run instantly."""
    with patch("tenacity.nap.time.sleep"):
        yield


def test_service_account_find_user_by_email_paginated(settings):
    """find_user_by_email() always uses /external_api/v1.0/users/ regardless of auth mode."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"
    backend._token_expires_at = timezone.now() + timedelta(hours=1)

    with patch_drive_http() as mock_requests:
        mock_requests.get.return_value.json.return_value = {
            "results": [{"id": "user-uuid", "email": "alice@example.com"}]
        }
        mock_requests.get.return_value.raise_for_status = MagicMock()
        result = backend.find_user_by_email("alice@example.com")

    mock_requests.get.assert_called_once_with(
        "https://drive.example.com/external_api/v1.0/users/",
        params={"q": "alice@example.com"},
        headers={"Authorization": "Bearer tok"},
        timeout=30,
    )
    assert result == {"id": "user-uuid", "email": "alice@example.com"}


def test_service_account_find_user_by_email_retries_on_timeout_then_succeeds(settings):
    """A transient ReadTimeout is retried and succeeds on the next attempt."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"
    backend._token_expires_at = timezone.now() + timedelta(hours=1)

    success_response = MagicMock()
    success_response.raise_for_status = MagicMock()
    success_response.json.return_value = {
        "results": [{"id": "user-uuid", "email": "alice@example.com"}]
    }

    with patch_drive_http() as mock_requests:
        mock_requests.get.side_effect = [Timeout("timed out"), success_response]
        result = backend.find_user_by_email("alice@example.com")

    assert mock_requests.get.call_count == 2
    assert result == {"id": "user-uuid", "email": "alice@example.com"}


def test_service_account_find_user_by_email_flat_list(settings):
    """find_user_by_email() handles a flat list response."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"
    backend._token_expires_at = timezone.now() + timedelta(hours=1)

    with patch_drive_http() as mock_requests:
        mock_requests.get.return_value.json.return_value = [
            {"id": "user-uuid", "email": "alice@example.com"}
        ]
        mock_requests.get.return_value.raise_for_status = MagicMock()
        result = backend.find_user_by_email("alice@example.com")

    assert result == {"id": "user-uuid", "email": "alice@example.com"}


def test_service_account_find_user_by_email_not_found(settings):
    """find_user_by_email() returns None when Drive returns an empty result set."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"
    backend._token_expires_at = timezone.now() + timedelta(hours=1)

    with patch_drive_http() as mock_requests:
        mock_requests.get.return_value.json.return_value = {"results": []}
        mock_requests.get.return_value.raise_for_status = MagicMock()
        result = backend.find_user_by_email("unknown@example.com")

    assert result is None


def _find_user(settings, email, results):
    """find_user_by_email(email) when Drive's user search returns results."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"
    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"
    backend._token_expires_at = timezone.now() + timedelta(hours=1)

    with patch_drive_http() as mock_requests:
        mock_requests.get.return_value.json.return_value = {"results": results}
        return backend.find_user_by_email(email)


def test_service_account_find_user_by_email_ignores_users_with_another_email(
    settings,
):
    """Drive's search is fuzzy: a user whose email only looks like the one
    searched must not be taken for them (they would be shared the workspace)."""
    result = _find_user(
        settings,
        "dinum_poc2@test.lan",
        [{"id": "owner-uuid", "email": "dinum_poc3@test.lan"}],
    )

    assert result is None


def test_service_account_find_user_by_email_picks_the_exact_match(settings):
    """Among similar users, the one with the searched email is returned."""
    result = _find_user(
        settings,
        "alice@example.com",
        [
            {"id": "other-uuid", "email": "alice.martin@example.com"},
            {"id": "alice-uuid", "email": "alice@example.com"},
        ],
    )

    assert result == {"id": "alice-uuid", "email": "alice@example.com"}


def test_service_account_find_user_by_email_ignores_case(settings):
    """Emails differing only by case designate the same user."""
    result = _find_user(
        settings,
        "Alice@Example.com",
        [{"id": "alice-uuid", "email": "alice@example.com"}],
    )

    assert result == {"id": "alice-uuid", "email": "alice@example.com"}


def _me_response(status_code, payload=None):
    """A users/me/ response with status_code."""
    response = MagicMock(status_code=status_code)
    response.json.return_value = payload
    if status_code >= 400:
        response.raise_for_status.side_effect = HTTPError(response=response)
    return response


def _get_me(settings, *status_codes, payload=None):
    """get_me() when Drive answers users/me/ with status_codes in turn."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"
    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"
    backend._token_expires_at = timezone.now() + timedelta(hours=1)

    with (
        patch_drive_http() as mock_requests,
        patch.object(backend, "_refresh") as mock_refresh,
    ):
        mock_requests.get.side_effect = [
            _me_response(code, payload) for code in status_codes
        ]
        result = backend.get_me()
    return result, mock_requests, mock_refresh


def test_get_me_returns_the_token_holder(settings):
    """get_me() returns the Drive user the token belongs to."""
    result, mock_requests, mock_refresh = _get_me(
        settings, 200, payload={"id": "alice-uuid"}
    )

    mock_requests.get.assert_called_once_with(
        "https://drive.example.com/external_api/v1.0/users/me/",
        headers={"Authorization": "Bearer tok"},
        timeout=30,
    )
    assert result == {"id": "alice-uuid"}
    mock_refresh.assert_not_called()


def test_get_me_returns_none_when_drive_does_not_know_the_user(settings):
    """Drive still answering 401 to a fresh token: the user has no account."""
    result, mock_requests, mock_refresh = _get_me(settings, 401, 401)

    assert result is None
    assert mock_requests.get.call_count == 2
    mock_refresh.assert_called_once()


def test_get_me_refreshes_a_token_refused_before_its_expiry(settings):
    """A token revoked before it expires gets a 401 too: a fresh one gets through."""
    result, _, mock_refresh = _get_me(settings, 401, 200, payload={"id": "alice-uuid"})

    assert result == {"id": "alice-uuid"}
    mock_refresh.assert_called_once()


def test_get_me_does_not_refresh_without_a_refresh_token(settings):
    """Without a refresh token, a 401 can only be read as a missing account."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"
    user = MagicMock(
        oidc_access_token=encrypt_token("tok"),
        oidc_refresh_token="",
        oidc_token_expires_at=timezone.now() + timedelta(hours=1),
    )

    with patch_drive_http() as mock_requests:
        mock_requests.get.return_value = _me_response(401)
        result = DriveUserTokenBackend(user).get_me()

    assert result is None
    mock_requests.post.assert_not_called()


def _token_user(expires_at):
    """A user whose stored access token expires at expires_at."""
    return MagicMock(
        email="alice@example.com",
        oidc_access_token=encrypt_token("tok"),
        oidc_refresh_token=encrypt_token("refresh-tok"),
        oidc_token_expires_at=expires_at,
    )


def _token_response(status_code, payload):
    """A token endpoint response with status_code."""
    response = MagicMock(status_code=status_code)
    response.json.return_value = payload
    if status_code >= 400:
        response.raise_for_status.side_effect = HTTPError(response=response)
    return response


def test_get_me_asks_to_log_in_again_when_the_forced_refresh_is_refused(settings):
    """A token Drive refuses and that cannot be refreshed may have been revoked:
    logging in again gives a fresh one, so it is not taken as a missing account."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"
    settings.OIDC_OP_TOKEN_ENDPOINT = "https://oidc.example.com/token"
    user = _token_user(timezone.now() + timedelta(hours=1))

    with patch_drive_http() as mock_requests:
        mock_requests.get.return_value = _me_response(401)
        mock_requests.post.return_value = _token_response(
            400, {"error": "invalid_grant"}
        )
        with pytest.raises(DriveTokenExpired):
            DriveUserTokenBackend(user).get_me()

    mock_requests.post.assert_called_once()


def test_get_me_does_not_refresh_a_token_it_just_refreshed(settings):
    """A 401 to a token refreshed for this very call means a missing account."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"
    settings.OIDC_OP_TOKEN_ENDPOINT = "https://oidc.example.com/token"
    user = _token_user(timezone.now() - timedelta(seconds=1))

    with patch_drive_http() as mock_requests:
        mock_requests.get.return_value = _me_response(401)
        mock_requests.post.return_value = _token_response(
            200, {"access_token": "fresh-tok", "expires_in": 300}
        )
        result = DriveUserTokenBackend(user).get_me()

    assert result is None
    mock_requests.post.assert_called_once()
    mock_requests.get.assert_called_once()


def test_get_me_raises_on_other_errors(settings):
    """Any other refusal is not a missing account."""
    with pytest.raises(HTTPError):
        _get_me(settings, 403)


def test_get_me_retries_on_timeout_then_succeeds(settings):
    """get_me() goes through the session, which retries a transient timeout."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"
    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"
    backend._token_expires_at = timezone.now() + timedelta(hours=1)

    success_response = MagicMock(status_code=200)
    success_response.json.return_value = {"id": "alice-uuid"}
    with patch_drive_http() as mock_requests:
        mock_requests.get.side_effect = [Timeout("timed out"), success_response]
        result = backend.get_me()

    assert mock_requests.get.call_count == 2
    assert result == {"id": "alice-uuid"}
