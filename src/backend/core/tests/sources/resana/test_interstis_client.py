"""Tests for InterstisClient — Interstis GED API HTTP client."""

import logging
import ssl
from unittest.mock import MagicMock, patch

import pytest
import requests as req_lib
import urllib3

from core.sources.resana import interstis_client
from core.sources.resana.interstis_client import InterstisClient


@pytest.fixture(autouse=True)
def no_retry_delay():
    """Skip real sleeping so retry tests run instantly."""
    with patch("tenacity.nap.time.sleep"):
        yield


def _http_error(status_code):
    response = MagicMock()
    response.status_code = status_code
    error = req_lib.HTTPError(f"{status_code} error")
    error.response = response
    return error


# ---------------------------------------------------------------------------
# Group 1 — Constructor
# ---------------------------------------------------------------------------


def test_client_sets_bearer_token_header(settings):
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"

    with patch("core.sources.resana.interstis_client.RetryingSession") as mock_session:
        client = InterstisClient("my-token")

    mock_session.return_value.headers.__setitem__.assert_called_once_with(
        "Authorization", "Bearer my-token"
    )
    assert client.token == "my-token"


def test_client_creates_retrying_session_on_init(settings):
    """The session retries transient failures using the RESANA_RETRY_* settings."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"

    with patch("core.sources.resana.interstis_client.RetryingSession") as mock_session:
        InterstisClient("tok")

    mock_session.assert_called_once_with("RESANA", interstis_client.logger)


# ---------------------------------------------------------------------------
# Group 2 — get_workspaces()
# ---------------------------------------------------------------------------


def test_get_workspaces_returns_members_from_single_page(settings):
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"

    page_1 = {
        "hydra:member": [
            {"uuid": "ws-1", "name": "Workspace A", "isPersonalWorkspace": False},
            {"uuid": "ws-2", "name": "Workspace B", "isPersonalWorkspace": False},
        ],
        "hydra:totalItems": 2,
    }

    client = InterstisClient("fake-token")
    client.session = MagicMock()
    client.session.get.return_value.json.return_value = page_1
    client.session.get.return_value.raise_for_status = MagicMock()

    result = client.get_workspaces()

    client.session.get.assert_called_once_with(
        "https://resana.example.com/api/api/workspaces",
        params={"page": 1, "itemsPerPage": 500},
        timeout=30,
    )
    assert len(result) == 2
    assert result[0]["uuid"] == "ws-1"


def test_get_workspaces_paginates_until_all_fetched(settings):
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"

    page_1 = {
        "hydra:member": [{"uuid": f"ws-{i}"} for i in range(500)],
        "hydra:totalItems": 750,
    }
    page_2 = {
        "hydra:member": [{"uuid": f"ws-{i}"} for i in range(500, 750)],
        "hydra:totalItems": 750,
    }

    client = InterstisClient("tok")
    client.session = MagicMock()
    client.session.get.return_value.raise_for_status = MagicMock()
    client.session.get.return_value.json.side_effect = [page_1, page_2]

    result = client.get_workspaces()

    assert client.session.get.call_count == 2
    assert len(result) == 750


# ---------------------------------------------------------------------------
# Group 3 — explore()
# ---------------------------------------------------------------------------


def test_explore_returns_folders_and_files(settings):
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"

    explore_response = {
        "hydra:member": [
            {
                "uuid": "root-uuid",
                "name": "root",
                "folders": [
                    {
                        "uuid": "folder-1",
                        "name": "Documents",
                        "folders": [],
                        "files": [],
                    },
                ],
                "files": [
                    {"uuid": "file-1", "name": "report", "extension": ".pdf"},
                ],
            }
        ],
        "hydra:totalItems": 1,
    }

    client = InterstisClient("tok")
    client.session = MagicMock()
    client.session.get.return_value.json.return_value = explore_response
    client.session.get.return_value.raise_for_status = MagicMock()

    result = client.explore("root-uuid")

    client.session.get.assert_called_once_with(
        "https://resana.example.com/api/api/targets/root-uuid/explore",
        params={"page": 1, "itemsPerPage": 500},
        timeout=30,
    )
    assert len(result) == 1
    assert result[0]["uuid"] == "root-uuid"
    assert result[0]["folders"][0]["uuid"] == "folder-1"
    assert result[0]["files"][0]["uuid"] == "file-1"


# ---------------------------------------------------------------------------
# Group 4 — download_file()
# ---------------------------------------------------------------------------


def test_download_file_writes_binary_content_to_path(settings, tmp_path):
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"

    destination = str(tmp_path / "output.pdf")
    file_content = b"%PDF binary content"

    mock_response = MagicMock()
    mock_response.iter_content.return_value = [file_content]
    mock_response.raise_for_status = MagicMock()
    mock_response.__enter__ = lambda s: mock_response
    mock_response.__exit__ = MagicMock(return_value=False)

    client = InterstisClient("tok")
    client.session = MagicMock()
    client.session.get.return_value = mock_response

    client.download_file("file-uuid-123", destination)

    client.session.get.assert_called_once_with(
        "https://resana.example.com/api/api/targets/file-uuid-123/download",
        stream=True,
        timeout=60,
        idempotent=False,
    )
    with open(destination, "rb") as f:
        assert f.read() == file_content


def test_download_file_raises_on_http_error(settings):
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"

    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = req_lib.HTTPError("404")
    mock_response.__enter__ = lambda s: mock_response
    mock_response.__exit__ = MagicMock(return_value=False)

    client = InterstisClient("tok")
    client.session = MagicMock()
    client.session.get.return_value = mock_response

    with pytest.raises(req_lib.HTTPError):
        client.download_file("missing-uuid", "/tmp/x.pdf")


def _success_response(content=b"content"):
    response = MagicMock()
    response.iter_content.return_value = [content]
    response.raise_for_status = MagicMock()
    response.__enter__ = lambda s: response
    response.__exit__ = MagicMock(return_value=False)
    return response


def _failing_response(status_code):
    response = MagicMock()
    response.raise_for_status.side_effect = _http_error(status_code)
    response.__enter__ = lambda s: response
    response.__exit__ = MagicMock(return_value=False)
    return response


def test_download_file_retries_on_timeout_then_succeeds(settings, tmp_path):
    """A transient network timeout is retried and eventually succeeds."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    destination = str(tmp_path / "output.pdf")

    client = InterstisClient("tok")
    client.session = MagicMock()
    client.session.get.side_effect = [
        req_lib.Timeout("timed out"),
        _success_response(),
    ]

    client.download_file("file-uuid", destination)

    assert client.session.get.call_count == 2


def test_download_file_retries_on_connection_error_then_succeeds(settings, tmp_path):
    """A transient connection error is retried and eventually succeeds."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    destination = str(tmp_path / "output.pdf")

    client = InterstisClient("tok")
    client.session = MagicMock()
    client.session.get.side_effect = [
        req_lib.ConnectionError("connection reset"),
        _success_response(),
    ]

    client.download_file("file-uuid", destination)

    assert client.session.get.call_count == 2


def test_download_file_retries_on_server_error_then_succeeds(settings, tmp_path):
    """A 504 Gateway Timeout from Interstis is retried and eventually succeeds."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    destination = str(tmp_path / "output.pdf")

    client = InterstisClient("tok")
    client.session = MagicMock()
    client.session.get.side_effect = [_failing_response(504), _success_response()]

    client.download_file("file-uuid", destination)

    assert client.session.get.call_count == 2


def test_download_file_does_not_retry_on_client_error(settings):
    """A 403 Forbidden is a permanent failure, not worth retrying."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"

    client = InterstisClient("tok")
    client.session = MagicMock()
    client.session.get.return_value = _failing_response(403)

    with pytest.raises(req_lib.HTTPError):
        client.download_file("file-uuid", "/tmp/x.pdf")

    assert client.session.get.call_count == 1


def test_download_file_retries_configured_max_attempts(settings):
    """The number of retry attempts before giving up follows RESANA_RETRY_MAX_ATTEMPTS."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    settings.RESANA_RETRY_MAX_ATTEMPTS = 2

    client = InterstisClient("tok")
    client.session = MagicMock()
    client.session.get.side_effect = req_lib.Timeout("timed out")

    with pytest.raises(req_lib.Timeout):
        client.download_file("file-uuid", "/tmp/x.pdf")

    assert client.session.get.call_count == 2


def test_download_file_wait_uses_configured_timing(settings, tmp_path):
    """Retry backoff timing follows RESANA_RETRY_WAIT_MULTIPLIER / RESANA_RETRY_WAIT_MIN."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    settings.RESANA_RETRY_WAIT_MULTIPLIER = 5
    settings.RESANA_RETRY_WAIT_MIN = 7
    destination = str(tmp_path / "output.pdf")

    client = InterstisClient("tok")
    client.session = MagicMock()
    client.session.get.side_effect = [
        req_lib.Timeout("timed out"),
        _success_response(),
    ]

    with patch("tenacity.nap.time.sleep") as mock_sleep:
        client.download_file("file-uuid", destination)

    mock_sleep.assert_called_once()
    assert mock_sleep.call_args[0][0] >= 7


def test_download_file_logs_before_sleeping_on_retry(settings, tmp_path):
    """A retry attempt is logged at INFO: it's recoverable, not a definitive failure."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    destination = str(tmp_path / "output.pdf")

    client = InterstisClient("tok")
    client.session = MagicMock()
    client.session.get.side_effect = [
        req_lib.Timeout("timed out"),
        _success_response(),
    ]

    with patch.object(interstis_client.logger, "log") as mock_log:
        client.download_file("file-uuid", destination)

    mock_log.assert_called_once()
    assert mock_log.call_args[0][0] == logging.INFO


def test_download_file_logs_error_on_final_failure(settings):
    """Once every attempt is exhausted, the definitive failure is logged at ERROR."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    settings.RESANA_RETRY_MAX_ATTEMPTS = 2

    client = InterstisClient("tok")
    client.session = MagicMock()
    client.session.get.side_effect = req_lib.Timeout("timed out")

    with (
        patch.object(interstis_client.logger, "error") as mock_error,
        pytest.raises(req_lib.Timeout),
    ):
        client.download_file("file-uuid", "/tmp/x.pdf")

    mock_error.assert_called_once()


# ---------------------------------------------------------------------------
# Group 5 — Retry on transient errors, through the real RetryingSession
# ---------------------------------------------------------------------------


@pytest.fixture(name="base_request")
def fixture_base_request():
    """Mock the requests.Session.request wrapped by RetryingSession."""
    with patch.object(req_lib.Session, "request") as mock_request:
        yield mock_request


def _json_response(payload, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    return response


def test_explore_is_retried_on_timeout(settings, base_request):
    """A timed out explore page is replayed instead of aborting the whole export."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    page = {"hydra:member": [{"uuid": "root-uuid"}], "hydra:totalItems": 1}
    base_request.side_effect = [req_lib.Timeout("read timed out"), _json_response(page)]

    result = InterstisClient("tok").explore("root-uuid")

    assert base_request.call_count == 2
    assert result == [{"uuid": "root-uuid"}]


def test_get_workspaces_retries_only_the_failing_page(settings, base_request):
    """A 5xx on the second page replays that page, not the whole pagination."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    page_1 = {
        "hydra:member": [{"uuid": f"ws-{i}"} for i in range(500)],
        "hydra:totalItems": 501,
    }
    page_2 = {"hydra:member": [{"uuid": "ws-500"}], "hydra:totalItems": 501}
    base_request.side_effect = [
        _json_response(page_1),
        _json_response({}, status_code=504),
        _json_response(page_2),
    ]

    result = InterstisClient("tok").get_workspaces()

    assert base_request.call_count == 3
    assert len(result) == 501
    pages = [call.kwargs["params"]["page"] for call in base_request.call_args_list]
    assert pages == [1, 2, 2]


def test_download_file_is_not_retried_twice_over(settings, base_request):
    """download_file already retries the whole download, so the session must not
    retry it again: attempts stay at RESANA_RETRY_MAX_ATTEMPTS, not its square."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    settings.RESANA_RETRY_MAX_ATTEMPTS = 3
    base_request.side_effect = req_lib.Timeout("read timed out")

    with pytest.raises(req_lib.Timeout):
        InterstisClient("tok").download_file("file-uuid", "/tmp/x.pdf")

    assert base_request.call_count == 3


def test_download_file_is_retried_when_the_body_is_cut_short(settings, tmp_path):
    """A connection dropped mid-stream raises ChunkedEncodingError from
    iter_content, outside the session: the download decorator retries it."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    cut_short = _success_response()
    cut_short.iter_content.side_effect = req_lib.exceptions.ChunkedEncodingError(
        "IncompleteRead"
    )

    client = InterstisClient("tok")
    client.session = MagicMock()
    client.session.get.side_effect = [cut_short, _success_response()]

    client.download_file("file-uuid", str(tmp_path / "output.pdf"))

    assert client.session.get.call_count == 2


def _certificate_error():
    """Build the SSLError requests raises when the certificate can't be verified."""
    reason = urllib3.exceptions.SSLError(
        ssl.SSLCertVerificationError("certificate verify failed")
    )
    return req_lib.exceptions.SSLError(
        urllib3.exceptions.MaxRetryError(None, "/x", reason=reason)
    )


def test_download_file_is_not_retried_on_certificate_error(settings):
    """A certificate that can't be verified fails every attempt: raised at once."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"

    client = InterstisClient("tok")
    client.session = MagicMock()
    client.session.get.side_effect = _certificate_error()

    with pytest.raises(req_lib.exceptions.SSLError):
        client.download_file("file-uuid", "/tmp/x.pdf")

    assert client.session.get.call_count == 1
