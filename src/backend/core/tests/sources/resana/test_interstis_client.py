"""Tests for InterstisClient — Interstis GED API HTTP client."""

# pylint: disable=redefined-outer-name  # pytest fixtures intentionally shadow outer names

import json
import logging
import re
import ssl
from unittest.mock import patch

import pytest
import responses
import urllib3
from requests.exceptions import ChunkedEncodingError, HTTPError, SSLError, Timeout
from requests.exceptions import ConnectionError as RequestsConnectionError

from core.retrying_session import RetryingSession
from core.sources.resana.interstis_client import InterstisClient

_API = "https://resana.example.com/api"


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.RESANA_API_ENDPOINT = _API
    settings.RESANA_RETRY_MAX_ATTEMPTS = 1


@pytest.fixture(autouse=True)
def no_retry_delay():
    """Skip real sleeping so retry tests run instantly."""
    with patch("tenacity.nap.time.sleep") as sleep:
        yield sleep


def _client():
    client = InterstisClient("my-token")
    assert isinstance(client.session, RetryingSession)
    return client


def _script(callback):
    recorded = responses.RequestsMock(assert_all_requests_are_fired=False)
    for method in (responses.GET, responses.POST):
        recorded.add_callback(method, re.compile(r"https://"), callback=callback)
    return recorded


def _json(payload, status=200):
    return status, {"Content-Type": "application/json"}, json.dumps(payload)


def test_client_sends_the_bearer_token():
    """The client calls Drive's sibling API with the token it was given."""

    def ok(_request):
        return _json({"hydra:member": [], "hydra:totalItems": 0})

    client = _client()
    with _script(ok) as recorded:
        client.get_workspaces()
        sent = recorded.calls[0].request
        assert sent.headers["Authorization"] == "Bearer my-token"
        assert sent.url.startswith(f"{_API}/api/workspaces")


def test_get_workspaces_returns_members_from_single_page():
    page = {
        "hydra:member": [
            {"uuid": "ws-1", "name": "Workspace A"},
            {"uuid": "ws-2", "name": "Workspace B"},
        ],
        "hydra:totalItems": 2,
    }

    def ok(request):
        assert "page=1" in request.url
        assert "itemsPerPage=500" in request.url
        return _json(page)

    with _script(ok):
        result = _client().get_workspaces()

    assert [item["uuid"] for item in result] == ["ws-1", "ws-2"]


def test_get_workspaces_paginates_until_all_fetched():
    def pages(request):
        page = 1 if "page=1" in request.url else 2
        if page == 1:
            members = [{"uuid": f"ws-{i}"} for i in range(500)]
        else:
            members = [{"uuid": f"ws-{i}"} for i in range(500, 750)]
        return _json({"hydra:member": members, "hydra:totalItems": 750})

    with _script(pages) as recorded:
        result = _client().get_workspaces()
        assert len(recorded.calls) == 2

    assert len(result) == 750


def test_explore_returns_folders_and_files():
    payload = {
        "hydra:member": [
            {
                "uuid": "root-uuid",
                "folders": [{"uuid": "folder-1", "name": "Documents"}],
                "files": [{"uuid": "file-1", "name": "report", "extension": ".pdf"}],
            }
        ],
        "hydra:totalItems": 1,
    }

    def ok(request):
        assert request.url.startswith(f"{_API}/api/targets/root-uuid/explore")
        return _json(payload)

    with _script(ok):
        result = _client().explore("root-uuid")

    assert result[0]["folders"][0]["uuid"] == "folder-1"
    assert result[0]["files"][0]["uuid"] == "file-1"


def test_download_file_writes_binary_content_to_path(tmp_path):
    destination = tmp_path / "output.pdf"

    def ok(request):
        assert request.url.endswith("/targets/file-uuid-123/download")
        return 200, {}, b"%PDF binary content"

    with _script(ok):
        _client().download_file("file-uuid-123", str(destination))

    assert destination.read_bytes() == b"%PDF binary content"


def test_download_file_raises_on_http_error():
    def missing(_request):
        return 404, {}, "missing"

    with _script(missing), pytest.raises(HTTPError):
        _client().download_file("missing-uuid", "/tmp/x.pdf")


def test_download_file_retries_on_timeout_then_succeeds(settings, tmp_path):
    """A transient network timeout is retried and eventually succeeds."""
    settings.RESANA_RETRY_MAX_ATTEMPTS = 2
    destination = tmp_path / "output.pdf"
    attempts = {"n": 0}

    def flaky(_request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise Timeout("timed out")
        return 200, {}, b"content"

    with _script(flaky):
        _client().download_file("file-uuid", str(destination))

    assert attempts["n"] == 2
    assert destination.read_bytes() == b"content"


def test_download_file_retries_on_connection_error_then_succeeds(settings, tmp_path):
    """A transient connection error is retried and eventually succeeds."""
    settings.RESANA_RETRY_MAX_ATTEMPTS = 2
    attempts = {"n": 0}

    def flaky(_request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RequestsConnectionError("connection reset")
        return 200, {}, b"content"

    with _script(flaky):
        _client().download_file("file-uuid", str(tmp_path / "output.pdf"))

    assert attempts["n"] == 2


def test_download_file_retries_on_server_error_then_succeeds(settings, tmp_path):
    """A 504 Gateway Timeout from Interstis is retried and eventually succeeds."""
    settings.RESANA_RETRY_MAX_ATTEMPTS = 2
    attempts = {"n": 0}

    def flaky(_request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return 504, {}, "down"
        return 200, {}, b"content"

    with _script(flaky):
        _client().download_file("file-uuid", str(tmp_path / "output.pdf"))

    assert attempts["n"] == 2


def test_download_file_does_not_retry_on_client_error():
    """A 403 Forbidden is a permanent failure, not worth retrying."""
    attempts = {"n": 0}

    def forbidden(_request):
        attempts["n"] += 1
        return 403, {}, "no"

    with _script(forbidden), pytest.raises(HTTPError):
        _client().download_file("file-uuid", "/tmp/x.pdf")

    assert attempts["n"] == 1


def test_download_file_retries_configured_max_attempts(settings):
    """The number of retry attempts before giving up follows RESANA_RETRY_MAX_ATTEMPTS."""
    settings.RESANA_RETRY_MAX_ATTEMPTS = 2
    attempts = {"n": 0}

    def down(_request):
        attempts["n"] += 1
        raise Timeout("timed out")

    with _script(down), pytest.raises(Timeout):
        _client().download_file("file-uuid", "/tmp/x.pdf")

    assert attempts["n"] == 2


def test_download_file_wait_uses_configured_timing(settings, tmp_path, no_retry_delay):
    """Retry backoff timing follows RESANA_RETRY_WAIT_MULTIPLIER / RESANA_RETRY_WAIT_MIN."""
    settings.RESANA_RETRY_MAX_ATTEMPTS = 2
    settings.RESANA_RETRY_WAIT_MULTIPLIER = 5
    settings.RESANA_RETRY_WAIT_MIN = 7
    attempts = {"n": 0}

    def flaky(_request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise Timeout("timed out")
        return 200, {}, b"content"

    with _script(flaky):
        _client().download_file("file-uuid", str(tmp_path / "output.pdf"))

    no_retry_delay.assert_called()
    assert no_retry_delay.call_args[0][0] >= 7


def test_download_file_logs_before_sleeping_on_retry(settings, tmp_path, caplog):
    """A retry attempt is logged at INFO: it's recoverable, not a definitive failure."""
    settings.RESANA_RETRY_MAX_ATTEMPTS = 2
    attempts = {"n": 0}

    def flaky(_request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise Timeout("timed out")
        return 200, {}, b"content"

    with (
        caplog.at_level(logging.INFO),
        _script(flaky),
    ):
        _client().download_file("file-uuid", str(tmp_path / "output.pdf"))

    assert any(record.levelno == logging.INFO for record in caplog.records)


def test_download_file_logs_error_on_final_failure(settings, caplog):
    """Once every attempt is exhausted, the definitive failure is logged at ERROR."""
    settings.RESANA_RETRY_MAX_ATTEMPTS = 2

    def down(_request):
        raise Timeout("timed out")

    with (
        caplog.at_level(logging.ERROR),
        _script(down),
        pytest.raises(Timeout),
    ):
        _client().download_file("file-uuid", "/tmp/x.pdf")

    assert "giving up after 2 attempt" in caplog.text


def test_explore_is_retried_on_timeout(settings):
    """A timed out explore page is replayed instead of aborting the whole export."""
    settings.RESANA_RETRY_MAX_ATTEMPTS = 2
    attempts = {"n": 0}
    page = {"hydra:member": [{"uuid": "root-uuid"}], "hydra:totalItems": 1}

    def flaky(_request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise Timeout("read timed out")
        return _json(page)

    with _script(flaky):
        result = _client().explore("root-uuid")

    assert attempts["n"] == 2
    assert result == [{"uuid": "root-uuid"}]


def test_get_workspaces_retries_only_the_failing_page(settings):
    """A 5xx on the second page replays that page, not the whole pagination."""
    settings.RESANA_RETRY_MAX_ATTEMPTS = 2
    pages = []

    def flaky(request):
        page = 1 if "page=1" in request.url else 2
        pages.append(page)
        if page == 2 and pages.count(2) == 1:
            return 504, {}, "down"
        if page == 1:
            members = [{"uuid": f"ws-{i}"} for i in range(500)]
        else:
            members = [{"uuid": "ws-500"}]
        return _json({"hydra:member": members, "hydra:totalItems": 501})

    with _script(flaky):
        result = _client().get_workspaces()

    assert pages == [1, 2, 2]
    assert len(result) == 501


def test_download_file_is_not_retried_twice_over(settings):
    """download_file already retries the whole download, so the session must not
    retry it again: attempts stay at RESANA_RETRY_MAX_ATTEMPTS, not its square."""
    settings.RESANA_RETRY_MAX_ATTEMPTS = 3
    attempts = {"n": 0}

    def down(_request):
        attempts["n"] += 1
        raise Timeout("read timed out")

    with _script(down), pytest.raises(Timeout):
        _client().download_file("file-uuid", "/tmp/x.pdf")

    assert attempts["n"] == 3


def test_download_file_is_retried_when_the_body_is_cut_short(settings, tmp_path):
    """A connection dropped mid-stream raises ChunkedEncodingError from
    iter_content, outside the session: the download decorator retries it."""
    settings.RESANA_RETRY_MAX_ATTEMPTS = 2
    attempts = {"n": 0}

    def flaky(_request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ChunkedEncodingError("IncompleteRead")
        return 200, {}, b"content"

    destination = tmp_path / "output.pdf"
    with _script(flaky):
        _client().download_file("file-uuid", str(destination))

    assert attempts["n"] == 2
    assert destination.read_bytes() == b"content"


def _certificate_error():
    """Build the SSLError requests raises when the certificate can't be verified."""
    reason = urllib3.exceptions.SSLError(
        ssl.SSLCertVerificationError("certificate verify failed")
    )
    return SSLError(urllib3.exceptions.MaxRetryError(None, "/x", reason=reason))


def test_download_file_is_not_retried_on_certificate_error():
    """A certificate that can't be verified fails every attempt: raised at once."""
    attempts = {"n": 0}

    def bad_cert(_request):
        attempts["n"] += 1
        raise _certificate_error()

    with _script(bad_cert), pytest.raises(SSLError):
        _client().download_file("file-uuid", "/tmp/x.pdf")

    assert attempts["n"] == 1
