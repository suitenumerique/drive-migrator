"""Tests for the shared tenacity retry_error_callback helper."""

from unittest.mock import MagicMock

import pytest

from core.retry_utils import log_final_failure_and_reraise


def _retry_state(exception, attempt_number=3, fn_name="download_file"):
    outcome = MagicMock()
    outcome.exception.return_value = exception
    retry_state = MagicMock()
    retry_state.outcome = outcome
    retry_state.attempt_number = attempt_number
    retry_state.fn.__name__ = fn_name
    return retry_state


def test_log_final_failure_and_reraise_reraises_the_original_exception():
    """The callback must not swallow the exception that exhausted all retries."""
    error = ConnectionError("connection reset")
    callback = log_final_failure_and_reraise(MagicMock())

    with pytest.raises(ConnectionError) as excinfo:
        callback(_retry_state(error))

    assert excinfo.value is error


def test_log_final_failure_and_reraise_logs_an_error():
    """The definitive failure, once every attempt is exhausted, is logged at ERROR."""
    error = ConnectionError("connection reset")
    logger = MagicMock()
    callback = log_final_failure_and_reraise(logger)

    with pytest.raises(ConnectionError):
        callback(_retry_state(error, attempt_number=3, fn_name="download_file"))

    logger.error.assert_called_once()
    args = logger.error.call_args[0]
    assert "download_file" in args
    assert 3 in args
    assert error in args
