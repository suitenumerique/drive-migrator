"""Tests for DriveBackend retry timing/attempts settings and retry logging.

Split out of test_drive_backend.py to stay under pylint's max-module-lines.
"""

import logging
from datetime import timedelta
from unittest.mock import MagicMock, mock_open, patch

from django.utils import timezone

import pytest
from cryptography.fernet import Fernet
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout

from core.destinations.drive import drive_backend
from core.destinations.drive.drive_backend import DriveServiceAccountBackend

TEST_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def set_encryption_key(settings):
    settings.OIDC_TOKENS_ENCRYPTION_KEY = TEST_KEY


@pytest.fixture(autouse=True)
def no_retry_delay():
    """Skip real sleeping (tenacity's and our manual loop's) so retry tests run instantly."""
    with (
        patch("tenacity.nap.time.sleep"),
        patch("core.destinations.drive.drive_backend.time.sleep"),
    ):
        yield


def test_service_account_upload_to_s3_retries_configured_max_attempts(settings):
    """The number of retry attempts before giving up follows DRIVE_RETRY_MAX_ATTEMPTS."""
    settings.DRIVE_RETRY_MAX_ATTEMPTS = 2
    policy_url = "https://s3.example.com/file.pdf?sig=x"

    with (
        patch("core.destinations.drive.drive_backend.requests") as mock_requests,
        patch("builtins.open", mock_open(read_data=b"binary content")),
        pytest.raises(RequestsConnectionError),
    ):
        mock_requests.put.side_effect = RequestsConnectionError("connection reset")
        DriveServiceAccountBackend().upload_to_s3(policy_url, "/tmp/doc.pdf")

    assert mock_requests.put.call_count == 2


def test_service_account_upload_to_s3_wait_uses_configured_timing(settings):
    """Retry backoff timing follows DRIVE_RETRY_WAIT_MULTIPLIER / DRIVE_RETRY_WAIT_MIN."""
    settings.DRIVE_RETRY_WAIT_MULTIPLIER = 5
    settings.DRIVE_RETRY_WAIT_MIN = 7
    policy_url = "https://s3.example.com/file.pdf?sig=x"

    success_response = MagicMock()
    success_response.raise_for_status = MagicMock()

    with (
        patch("core.destinations.drive.drive_backend.requests") as mock_requests,
        patch("builtins.open", mock_open(read_data=b"binary content")),
        patch("tenacity.nap.time.sleep") as mock_sleep,
    ):
        mock_requests.put.side_effect = [
            RequestsConnectionError("connection reset"),
            success_response,
        ]
        DriveServiceAccountBackend().upload_to_s3(policy_url, "/tmp/doc.pdf")

    mock_sleep.assert_called_once()
    assert mock_sleep.call_args[0][0] >= 7


def test_service_account_upload_to_s3_logs_before_sleeping_on_retry():
    """A retry attempt is logged at INFO: it's recoverable, not a definitive failure."""
    policy_url = "https://s3.example.com/file.pdf?sig=x"

    success_response = MagicMock()
    success_response.raise_for_status = MagicMock()

    with (
        patch("core.destinations.drive.drive_backend.requests") as mock_requests,
        patch("builtins.open", mock_open(read_data=b"binary content")),
        patch.object(drive_backend.logger, "log") as mock_log,
    ):
        mock_requests.put.side_effect = [
            RequestsConnectionError("connection reset"),
            success_response,
        ]
        DriveServiceAccountBackend().upload_to_s3(policy_url, "/tmp/doc.pdf")

    mock_log.assert_called_once()
    assert mock_log.call_args[0][0] == logging.INFO


def test_service_account_upload_to_s3_logs_error_on_final_failure(settings):
    """Once every attempt is exhausted, the definitive failure is logged at ERROR."""
    settings.DRIVE_RETRY_MAX_ATTEMPTS = 2
    policy_url = "https://s3.example.com/file.pdf?sig=x"

    with (
        patch("core.destinations.drive.drive_backend.requests") as mock_requests,
        patch("builtins.open", mock_open(read_data=b"binary content")),
        patch.object(drive_backend.logger, "error") as mock_error,
        pytest.raises(RequestsConnectionError),
    ):
        mock_requests.put.side_effect = RequestsConnectionError("connection reset")
        DriveServiceAccountBackend().upload_to_s3(policy_url, "/tmp/doc.pdf")

    mock_error.assert_called_once()


def test_service_account_notify_upload_ended_retries_configured_max_attempts(settings):
    """The manual retry loop's attempt count follows DRIVE_RETRY_MAX_ATTEMPTS."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"
    settings.DRIVE_RETRY_MAX_ATTEMPTS = 2

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"  # pylint: disable=protected-access
    backend._token_expires_at = timezone.now() + timedelta(hours=1)  # pylint: disable=protected-access

    with (
        patch("core.destinations.drive.drive_backend.requests") as mock_requests,
        pytest.raises(Timeout),
    ):
        mock_requests.post.side_effect = Timeout("timed out")
        backend.notify_upload_ended("file-uuid")

    assert mock_requests.post.call_count == 2


def test_service_account_notify_upload_ended_logs_before_retrying(settings):
    """Each retry of the manual loop is logged at INFO: it's recoverable."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"  # pylint: disable=protected-access
    backend._token_expires_at = timezone.now() + timedelta(hours=1)  # pylint: disable=protected-access

    success_response = MagicMock()
    success_response.raise_for_status = MagicMock()

    with (
        patch("core.destinations.drive.drive_backend.requests") as mock_requests,
        patch.object(drive_backend.logger, "info") as mock_info,
    ):
        mock_requests.post.side_effect = [Timeout("timed out"), success_response]
        backend.notify_upload_ended("file-uuid")

    mock_info.assert_called_once()


def test_service_account_notify_upload_ended_logs_error_on_final_failure(settings):
    """Once every attempt is exhausted, the definitive failure is logged at ERROR."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"
    settings.DRIVE_RETRY_MAX_ATTEMPTS = 2

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"  # pylint: disable=protected-access
    backend._token_expires_at = timezone.now() + timedelta(hours=1)  # pylint: disable=protected-access

    with (
        patch("core.destinations.drive.drive_backend.requests") as mock_requests,
        patch.object(drive_backend.logger, "error") as mock_error,
        pytest.raises(Timeout),
    ):
        mock_requests.post.side_effect = Timeout("timed out")
        backend.notify_upload_ended("file-uuid")

    mock_error.assert_called_once()
