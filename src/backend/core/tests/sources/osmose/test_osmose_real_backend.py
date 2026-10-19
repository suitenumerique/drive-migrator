"""Tests for OsmoseRealBackend retry behavior on download_file() and fetch()."""

import logging
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest
import requests

from core.sources.osmose.osmose_real_backend import OsmoseRealBackend, get_logger


@pytest.fixture(autouse=True)
def no_retry_delay():
    """Skip real sleeping so retry tests run instantly."""
    with patch("tenacity.nap.time.sleep"):
        yield


def _backend():
    """Return an OsmoseRealBackend with a pre-set fake JWT, skipping key loading."""
    backend = OsmoseRealBackend()
    backend.jwt = "fake-jwt"
    return backend


def test_download_file_retries_configured_max_attempts(settings):
    """The number of retry attempts before giving up follows OSMOSE_RETRY_MAX_ATTEMPTS."""
    settings.OSMOSE_RETRY_MAX_ATTEMPTS = 2

    with (
        patch(
            "core.sources.osmose.osmose_real_backend.urllib.request.urlretrieve"
        ) as mock_urlretrieve,
        pytest.raises(URLError),
    ):
        mock_urlretrieve.side_effect = URLError("connection reset")
        _backend().download_file("http://osmose.example.com/file", "/tmp/x.pdf")

    assert mock_urlretrieve.call_count == 2


def test_download_file_retries_on_transient_error_then_succeeds():
    """A transient URLError is retried and succeeds on the next attempt."""
    with patch(
        "core.sources.osmose.osmose_real_backend.urllib.request.urlretrieve"
    ) as mock_urlretrieve:
        mock_urlretrieve.side_effect = [URLError("connection reset"), None]
        with patch("core.sources.osmose.osmose_real_backend.os.stat") as mock_stat:
            mock_stat.return_value.st_size = 42
            _backend().download_file("http://osmose.example.com/file", "/tmp/x.pdf")

    assert mock_urlretrieve.call_count == 2


def test_download_file_wait_uses_configured_timing(settings):
    """Retry backoff timing follows OSMOSE_RETRY_WAIT_MULTIPLIER / OSMOSE_RETRY_WAIT_MIN."""
    settings.OSMOSE_RETRY_WAIT_MULTIPLIER = 5
    settings.OSMOSE_RETRY_WAIT_MIN = 7

    with (
        patch(
            "core.sources.osmose.osmose_real_backend.urllib.request.urlretrieve"
        ) as mock_urlretrieve,
        patch("core.sources.osmose.osmose_real_backend.os.stat") as mock_stat,
        patch("tenacity.nap.time.sleep") as mock_sleep,
    ):
        mock_urlretrieve.side_effect = [URLError("connection reset"), None]
        mock_stat.return_value.st_size = 42
        _backend().download_file("http://osmose.example.com/file", "/tmp/x.pdf")

    mock_sleep.assert_called_once()
    assert mock_sleep.call_args[0][0] >= 7


def test_download_file_logs_before_sleeping_on_retry():
    """A retry attempt is logged at INFO: it's recoverable, not a definitive failure."""
    with (
        patch(
            "core.sources.osmose.osmose_real_backend.urllib.request.urlretrieve"
        ) as mock_urlretrieve,
        patch("core.sources.osmose.osmose_real_backend.os.stat") as mock_stat,
        patch.object(get_logger(), "log") as mock_log,
    ):
        mock_urlretrieve.side_effect = [URLError("connection reset"), None]
        mock_stat.return_value.st_size = 42
        _backend().download_file("http://osmose.example.com/file", "/tmp/x.pdf")

    mock_log.assert_called_once()
    assert mock_log.call_args[0][0] == logging.INFO


def test_download_file_logs_error_on_final_failure(settings):
    """Once every attempt is exhausted, the definitive failure is logged at ERROR."""
    settings.OSMOSE_RETRY_MAX_ATTEMPTS = 2

    with (
        patch(
            "core.sources.osmose.osmose_real_backend.urllib.request.urlretrieve"
        ) as mock_urlretrieve,
        patch.object(get_logger(), "error") as mock_error,
        pytest.raises(URLError),
    ):
        mock_urlretrieve.side_effect = URLError("connection reset")
        _backend().download_file("http://osmose.example.com/file", "/tmp/x.pdf")

    mock_error.assert_called_once()


def test_download_file_raises_accepted_404_without_retry(settings):
    """An accepted 404 is reported as a missing file, not as a silent success."""
    settings.OSMOSE_BACKEND_ACCEPT_404 = True
    settings.OSMOSE_RETRY_MAX_ATTEMPTS = 3
    url = "http://osmose.example.com/file"

    with (
        patch(
            "core.sources.osmose.osmose_real_backend.urllib.request.urlretrieve"
        ) as mock_urlretrieve,
        pytest.raises(FileNotFoundError, match="404"),
    ):
        mock_urlretrieve.side_effect = HTTPError(url, 404, "Not Found", None, None)
        _backend().download_file(url, "/tmp/x.pdf")

    assert mock_urlretrieve.call_count == 1


@pytest.fixture(name="base_request")
def fixture_base_request():
    """Mock the requests.Session.request wrapped by RetryingSession."""
    with patch.object(requests.Session, "request") as mock_request:
        yield mock_request


def _json_response(payload, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    if status_code >= 400:
        error = requests.HTTPError(f"{status_code} error")
        error.response = response
        response.raise_for_status.side_effect = error
    return response


def test_fetch_gets_the_endpoint_with_the_jwt(settings, base_request):
    """fetch() GETs the Osmose API with the bearer JWT and returns the JSON body."""
    settings.OSMOSE_API_ENDPOINT = "https://osmose.example.com/api"
    base_request.return_value = _json_response({"id": "ws-1"})

    assert _backend().fetch("/data/ws-1", params={"page": 1}) == {"id": "ws-1"}

    args, kwargs = base_request.call_args
    assert args == ("GET", "https://osmose.example.com/api/data/ws-1")
    assert kwargs["params"] == {"page": 1}
    assert kwargs["headers"]["Authorization"] == "Bearer fake-jwt"
    assert kwargs["timeout"] == 30


def test_fetch_is_retried_on_timeout(settings, base_request):
    """A timed out metadata call is replayed instead of aborting the export."""
    settings.OSMOSE_API_ENDPOINT = "https://osmose.example.com/api"
    base_request.side_effect = [
        requests.Timeout("read timed out"),
        _json_response({"id": "ws-1"}),
    ]

    assert _backend().fetch("/data/ws-1") == {"id": "ws-1"}
    assert base_request.call_count == 2


def test_fetch_is_retried_on_server_error(settings, base_request):
    """A 5xx from Osmose is transient: the call is replayed."""
    settings.OSMOSE_API_ENDPOINT = "https://osmose.example.com/api"
    base_request.side_effect = [
        _json_response({}, status_code=503),
        _json_response({"id": "ws-1"}),
    ]

    assert _backend().fetch("/data/ws-1") == {"id": "ws-1"}
    assert base_request.call_count == 2


def test_fetch_does_not_retry_on_client_error(settings, base_request):
    """A 4xx is permanent: fetch() raises right away."""
    settings.OSMOSE_API_ENDPOINT = "https://osmose.example.com/api"
    base_request.return_value = _json_response({}, status_code=403)

    with pytest.raises(requests.HTTPError):
        _backend().fetch("/data/ws-1")

    base_request.assert_called_once()


def test_fetch_retries_configured_max_attempts(settings, base_request):
    """fetch() gives up after OSMOSE_RETRY_MAX_ATTEMPTS attempts."""
    settings.OSMOSE_API_ENDPOINT = "https://osmose.example.com/api"
    settings.OSMOSE_RETRY_MAX_ATTEMPTS = 4
    base_request.side_effect = requests.Timeout("read timed out")

    with pytest.raises(requests.Timeout):
        _backend().fetch("/data/ws-1")

    assert base_request.call_count == 4
