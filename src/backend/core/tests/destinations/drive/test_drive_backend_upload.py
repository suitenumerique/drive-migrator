"""Tests for DriveBackend.upload_to_s3(), the S3 step of a Drive upload."""

from unittest.mock import MagicMock, patch

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import HTTPError

from core.destinations.drive.drive_backend import DriveServiceAccountBackend
from core.tests.destinations.drive.drive_http_mock import patch_drive_http


@pytest.fixture(autouse=True)
def no_retry_delay():
    """Skip real sleeping so retry tests run instantly."""
    with patch("tenacity.nap.time.sleep"):
        yield


def _doc(tmp_path, content=b"binary content"):
    """A real file to upload, since upload_to_s3() streams it from the disk."""
    path = tmp_path / "doc.pdf"
    path.write_bytes(content)
    return str(path)


def _record_put_bodies(mock_requests, response):
    """Make requests.put read and keep each body it is given, like a real PUT."""
    bodies = []

    def put(_url, data, **_kwargs):
        bodies.append(data if isinstance(data, bytes) else data.read())
        if isinstance(response, Exception):
            raise response
        return response

    mock_requests.put.side_effect = put
    return bodies


def test_service_account_upload_to_s3_streams_the_file(tmp_path):
    """upload_to_s3() PUTs the open file to the presigned URL (no Drive token):
    requests streams it with its Content-Length, instead of loading it in memory."""
    policy_url = "https://s3.example.com/file.pdf?sig=x"
    path = _doc(tmp_path)

    with patch_drive_http() as mock_requests:
        bodies = _record_put_bodies(mock_requests, MagicMock())
        DriveServiceAccountBackend().upload_to_s3(policy_url, path)

    _, kwargs = mock_requests.put.call_args
    assert mock_requests.put.call_args.args == (policy_url,)
    assert kwargs["timeout"] == 300
    assert kwargs["data"].name == path
    assert bodies == [b"binary content"]


def test_service_account_upload_to_s3_sends_an_empty_file_as_bytes(tmp_path):
    """Without content, requests would switch to chunked encoding, which S3
    presigned PUTs refuse: an empty file is sent as an empty body."""
    policy_url = "https://s3.example.com/file.pdf?sig=x"

    with patch_drive_http() as mock_requests:
        DriveServiceAccountBackend().upload_to_s3(policy_url, _doc(tmp_path, b""))

    mock_requests.put.assert_called_once_with(policy_url, data=b"", timeout=300)


def test_service_account_upload_to_s3_resends_the_whole_file_on_retry(tmp_path):
    """A retried PUT sends the file from its start again, not where the
    failed attempt stopped reading it."""
    policy_url = "https://s3.example.com/file.pdf?sig=x"

    with patch_drive_http() as mock_requests:
        bodies = _record_put_bodies(mock_requests, MagicMock())
        first_attempt = mock_requests.put.side_effect

        def fail_once(url, data, **kwargs):
            first_attempt(url, data, **kwargs)
            if len(bodies) == 1:
                raise RequestsConnectionError("connection reset")
            return MagicMock()

        mock_requests.put.side_effect = fail_once
        DriveServiceAccountBackend().upload_to_s3(policy_url, _doc(tmp_path))

    assert bodies == [b"binary content", b"binary content"]


def test_service_account_upload_to_s3_logs_response_body_on_error(tmp_path):
    """A failed S3 PUT logs the error response body before raising."""
    policy_url = "https://s3.example.com/file.pdf?sig=x"
    error_response = MagicMock()
    error_response.ok = False
    error_response.status_code = 400
    error_response.text = "<Error><Code>SignatureDoesNotMatch</Code></Error>"
    error_response.raise_for_status.side_effect = HTTPError(response=error_response)

    with (
        patch_drive_http() as mock_requests,
        patch("core.destinations.drive.drive_backend.logger") as mock_logger,
        pytest.raises(HTTPError),
    ):
        mock_requests.put.return_value = error_response
        DriveServiceAccountBackend().upload_to_s3(policy_url, _doc(tmp_path))

    mock_logger.error.assert_called_once()
    assert "SignatureDoesNotMatch" in mock_logger.error.call_args[0][3]


def test_service_account_upload_to_s3_retries_on_connection_error_then_succeeds(
    tmp_path
):
    """A transient ConnectionError on the S3 PUT is retried and succeeds."""
    policy_url = "https://s3.example.com/file.pdf?sig=x"

    success_response = MagicMock()
    success_response.raise_for_status = MagicMock()

    with (
        patch_drive_http() as mock_requests,
    ):
        mock_requests.put.side_effect = [
            RequestsConnectionError("connection reset"),
            success_response,
        ]
        DriveServiceAccountBackend().upload_to_s3(policy_url, _doc(tmp_path))

    assert mock_requests.put.call_count == 2
