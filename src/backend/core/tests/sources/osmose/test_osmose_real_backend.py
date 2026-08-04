"""Tests for OsmoseRealBackend.download_file() retry behavior."""

import logging
from unittest.mock import patch
from urllib.error import URLError

import pytest

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
