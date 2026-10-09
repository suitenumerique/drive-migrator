"""Tests for DriveBackend.find_user_by_email(), used to share migrated workspaces."""

# pylint: disable=protected-access

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.utils import timezone

import pytest
from requests.exceptions import Timeout

from core.destinations.drive.drive_backend import DriveServiceAccountBackend


@pytest.fixture(autouse=True)
def no_retry_delay():
    """Skip real sleeping so retry tests run instantly."""
    with patch("tenacity.nap.time.sleep"):
        yield


def test_service_account_find_user_by_email_paginated(settings):
    """find_user_by_email() always uses /api/v1.0/users/ regardless of auth mode."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"
    backend._token_expires_at = timezone.now() + timedelta(hours=1)

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
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

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
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

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
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

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
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

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
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
