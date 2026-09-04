"""Tests for DriveBackend retry timing/attempts settings and retry logging.

Split out of test_drive_backend.py to stay under pylint's max-module-lines.
"""

import logging
import uuid
from datetime import timedelta
from unittest.mock import MagicMock, mock_open, patch

from django.utils import timezone

import pytest
from cryptography.fernet import Fernet
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import HTTPError, Timeout

from core.destinations.drive import drive_backend
from core.destinations.drive.drive_backend import DriveServiceAccountBackend

TEST_KEY = Fernet.generate_key().decode()

# Item id generated client-side by create_folder/create_subfolder/create_file_item,
# so tests can assert on it and simulate Drive's GET /items/{id}/ existence check.
FAKE_ITEM_ID = "11111111-1111-1111-1111-111111111111"


def _server_error(status_code=500):
    """Build the HTTPError Drive raises on a transient 5xx, e.g. issue #208."""
    response = MagicMock()
    response.status_code = status_code
    error = HTTPError(f"{status_code} Server Error")
    error.response = response
    return error


def _not_found_response():
    """A GET /items/{id}/ response for an id Drive never created."""
    response = MagicMock()
    response.status_code = 404
    return response


def _found_response(data):
    """A GET /items/{id}/ response for an id Drive did create despite the error."""
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status = MagicMock()
    response.json.return_value = data
    return response


@pytest.fixture(autouse=True)
def fixed_item_id():
    """Make the client-generated item id deterministic for assertions."""
    with patch(
        "core.destinations.drive.drive_backend.uuid.uuid4",
        return_value=uuid.UUID(FAKE_ITEM_ID),
    ):
        yield


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


def test_service_account_create_subfolder_retries_on_server_error_then_succeeds(
    settings,
):
    """A transient 500 from Drive on subfolder creation is retried and succeeds (#208)."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"  # pylint: disable=protected-access
    backend._token_expires_at = timezone.now() + timedelta(hours=1)  # pylint: disable=protected-access

    error_response = MagicMock()
    error_response.raise_for_status.side_effect = _server_error()

    success_response = MagicMock()
    success_response.raise_for_status = MagicMock()
    success_response.json.return_value = {"id": FAKE_ITEM_ID}

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.side_effect = [error_response, success_response]
        mock_requests.get.return_value = _not_found_response()
        result = backend.create_subfolder("docs", parent_id="parent-uuid")

    assert mock_requests.post.call_count == 2
    assert result["id"] == FAKE_ITEM_ID


def test_service_account_create_subfolder_checks_existence_before_retrying(settings):
    """Between attempts, the item is looked up by its client-generated id."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"  # pylint: disable=protected-access
    backend._token_expires_at = timezone.now() + timedelta(hours=1)  # pylint: disable=protected-access

    error_response = MagicMock()
    error_response.raise_for_status.side_effect = _server_error()

    success_response = MagicMock()
    success_response.raise_for_status = MagicMock()
    success_response.json.return_value = {"id": FAKE_ITEM_ID}

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.side_effect = [error_response, success_response]
        mock_requests.get.return_value = _not_found_response()
        backend.create_subfolder("docs", parent_id="parent-uuid")

    mock_requests.get.assert_called_once_with(
        f"https://drive.example.com/external_api/v1.0/items/{FAKE_ITEM_ID}/",
        headers={"Authorization": "Bearer tok"},
        timeout=30,
    )


def test_service_account_create_subfolder_does_not_retry_on_client_error(settings):
    """A 4xx from Drive is a permanent error and must fail immediately, not be retried."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"  # pylint: disable=protected-access
    backend._token_expires_at = timezone.now() + timedelta(hours=1)  # pylint: disable=protected-access

    error_response = MagicMock()
    error_response.raise_for_status.side_effect = _server_error(400)

    with (
        patch("core.destinations.drive.drive_backend.requests") as mock_requests,
        pytest.raises(HTTPError),
    ):
        mock_requests.post.return_value = error_response
        backend.create_subfolder("docs", parent_id="parent-uuid")

    assert mock_requests.post.call_count == 1
    mock_requests.get.assert_not_called()


def test_service_account_create_subfolder_returns_existing_item_after_server_error(
    settings,
):
    """If Drive actually created the item before the 500, don't create a duplicate sibling.

    Drive silently renames a same-title duplicate (e.g. "docs_01") instead of rejecting
    it, so blindly retrying the POST would leave two folders instead of a clean error.
    """
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"  # pylint: disable=protected-access
    backend._token_expires_at = timezone.now() + timedelta(hours=1)  # pylint: disable=protected-access

    error_response = MagicMock()
    error_response.raise_for_status.side_effect = _server_error()

    existing_item = {"id": FAKE_ITEM_ID, "title": "docs"}

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value = error_response
        mock_requests.get.return_value = _found_response(existing_item)
        result = backend.create_subfolder("docs", parent_id="parent-uuid")

    assert result == existing_item
    assert mock_requests.post.call_count == 1


def test_service_account_create_subfolder_server_error_exhausts_attempts(settings):
    """A persistent 500 with the item never found is given up on after max attempts."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"
    settings.DRIVE_RETRY_MAX_ATTEMPTS = 2

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"  # pylint: disable=protected-access
    backend._token_expires_at = timezone.now() + timedelta(hours=1)  # pylint: disable=protected-access

    error_response = MagicMock()
    error_response.raise_for_status.side_effect = _server_error()

    with (
        patch("core.destinations.drive.drive_backend.requests") as mock_requests,
        pytest.raises(HTTPError),
    ):
        mock_requests.post.return_value = error_response
        mock_requests.get.return_value = _not_found_response()
        backend.create_subfolder("docs", parent_id="parent-uuid")

    assert mock_requests.post.call_count == 2
    assert mock_requests.get.call_count == 2


def test_service_account_create_subfolder_retries_when_existence_check_itself_fails(
    settings,
):
    """A failing existence check must not abort the whole retry loop (#208).

    If the GET used to check for a partially-applied write is itself hit by the
    same transient outage as the original POST, we don't know whether the item
    exists - but we must still get to retry the creation POST rather than giving
    up on attempt 1 while retry budget remains.
    """
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"  # pylint: disable=protected-access
    backend._token_expires_at = timezone.now() + timedelta(hours=1)  # pylint: disable=protected-access

    create_error_response = MagicMock()
    create_error_response.raise_for_status.side_effect = _server_error()

    check_error_response = MagicMock()
    check_error_response.status_code = 500
    check_error_response.raise_for_status.side_effect = _server_error()

    success_response = MagicMock()
    success_response.raise_for_status = MagicMock()
    success_response.json.return_value = {"id": FAKE_ITEM_ID}

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.side_effect = [create_error_response, success_response]
        mock_requests.get.return_value = check_error_response
        result = backend.create_subfolder("docs", parent_id="parent-uuid")

    assert mock_requests.post.call_count == 2
    assert result["id"] == FAKE_ITEM_ID


def test_service_account_create_folder_retries_on_server_error_then_succeeds(settings):
    """create_folder() shares the same retry-with-existence-check helper (#208)."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"  # pylint: disable=protected-access
    backend._token_expires_at = timezone.now() + timedelta(hours=1)  # pylint: disable=protected-access

    error_response = MagicMock()
    error_response.raise_for_status.side_effect = _server_error()

    success_response = MagicMock()
    success_response.raise_for_status = MagicMock()
    success_response.json.return_value = {"id": FAKE_ITEM_ID}

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.side_effect = [error_response, success_response]
        mock_requests.get.return_value = _not_found_response()
        result = backend.create_folder("My Workspace")

    assert mock_requests.post.call_count == 2
    assert result["id"] == FAKE_ITEM_ID


def test_service_account_create_file_item_retries_on_server_error_then_succeeds(
    settings,
):
    """create_file_item() shares the same retry-with-existence-check helper (#208)."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"  # pylint: disable=protected-access
    backend._token_expires_at = timezone.now() + timedelta(hours=1)  # pylint: disable=protected-access

    error_response = MagicMock()
    error_response.raise_for_status.side_effect = _server_error()

    success_response = MagicMock()
    success_response.raise_for_status = MagicMock()
    success_response.json.return_value = {"id": FAKE_ITEM_ID, "policy": "https://s3.x"}

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.side_effect = [error_response, success_response]
        mock_requests.get.return_value = _not_found_response()
        result = backend.create_file_item("doc.pdf", parent_id="folder-uuid")

    assert mock_requests.post.call_count == 2
    assert result["id"] == FAKE_ITEM_ID


def test_service_account_get_item_if_exists_returns_item_when_found(settings):
    """_get_item_if_exists() returns the parsed item on a 200."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"  # pylint: disable=protected-access
    backend._token_expires_at = timezone.now() + timedelta(hours=1)  # pylint: disable=protected-access

    item = {"id": FAKE_ITEM_ID, "title": "docs"}

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.get.return_value = _found_response(item)
        result = backend._get_item_if_exists(FAKE_ITEM_ID)  # pylint: disable=protected-access

    assert result == item


def test_service_account_get_item_if_exists_returns_none_on_404(settings):
    """_get_item_if_exists() returns None when Drive never created the item."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"  # pylint: disable=protected-access
    backend._token_expires_at = timezone.now() + timedelta(hours=1)  # pylint: disable=protected-access

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.get.return_value = _not_found_response()
        result = backend._get_item_if_exists(FAKE_ITEM_ID)  # pylint: disable=protected-access

    assert result is None


def test_service_account_get_item_if_exists_raises_on_other_error(settings):
    """_get_item_if_exists() doesn't swallow errors unrelated to a plain 404."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"  # pylint: disable=protected-access
    backend._token_expires_at = timezone.now() + timedelta(hours=1)  # pylint: disable=protected-access

    error_response = MagicMock()
    error_response.status_code = 500
    error_response.raise_for_status.side_effect = _server_error()

    with (
        patch("core.destinations.drive.drive_backend.requests") as mock_requests,
        pytest.raises(HTTPError),
    ):
        mock_requests.get.return_value = error_response
        backend._get_item_if_exists(FAKE_ITEM_ID)  # pylint: disable=protected-access


def test_service_account_get_item_if_exists_or_none_returns_item_when_found(settings):
    """_get_item_if_exists_or_none() returns the item on a 200, like the strict version."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"  # pylint: disable=protected-access
    backend._token_expires_at = timezone.now() + timedelta(hours=1)  # pylint: disable=protected-access

    item = {"id": FAKE_ITEM_ID, "title": "docs"}

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.get.return_value = _found_response(item)
        result = backend._get_item_if_exists_or_none(FAKE_ITEM_ID)  # pylint: disable=protected-access

    assert result == item


def test_service_account_get_item_if_exists_or_none_swallows_check_errors(settings):
    """Unlike _get_item_if_exists(), the check failing itself must not raise: the
    caller (the creation retry loop) needs to fall back to retrying the POST."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"  # pylint: disable=protected-access
    backend._token_expires_at = timezone.now() + timedelta(hours=1)  # pylint: disable=protected-access

    error_response = MagicMock()
    error_response.status_code = 500
    error_response.raise_for_status.side_effect = _server_error()

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.get.return_value = error_response
        result = backend._get_item_if_exists_or_none(FAKE_ITEM_ID)  # pylint: disable=protected-access

    assert result is None


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


def test_service_account_notify_upload_ended_retries_on_server_error_then_succeeds(
    settings,
):
    """A transient 5xx from Drive is retried by the manual loop and succeeds (#208)."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"  # pylint: disable=protected-access
    backend._token_expires_at = timezone.now() + timedelta(hours=1)  # pylint: disable=protected-access

    error_response = MagicMock()
    error_response.raise_for_status.side_effect = _server_error()

    success_response = MagicMock()
    success_response.raise_for_status = MagicMock()

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.side_effect = [error_response, success_response]
        backend.notify_upload_ended("file-uuid")  # must not raise

    assert mock_requests.post.call_count == 2


def test_service_account_notify_upload_ended_server_error_exhausts_attempts(settings):
    """A persistent 5xx from Drive is given up on after DRIVE_RETRY_MAX_ATTEMPTS."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"
    settings.DRIVE_RETRY_MAX_ATTEMPTS = 2

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"  # pylint: disable=protected-access
    backend._token_expires_at = timezone.now() + timedelta(hours=1)  # pylint: disable=protected-access

    error_response = MagicMock()
    error_response.raise_for_status.side_effect = _server_error()

    with (
        patch("core.destinations.drive.drive_backend.requests") as mock_requests,
        pytest.raises(HTTPError),
    ):
        mock_requests.post.return_value = error_response
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
