"""Tests for RetryingSession, the requests.Session that retries transient failures."""

import logging
import ssl
from unittest.mock import MagicMock, patch

import pytest
import requests
import urllib3
from requests.exceptions import ChunkedEncodingError, ProxyError, SSLError, Timeout
from requests.exceptions import ConnectionError as RequestsConnectionError

from core.retrying_session import RetryingSession, limited_retries

URL = "https://api.example.com/items"
LOGGER_NAME = "core.tests.retrying_session"


@pytest.fixture(autouse=True)
def no_retry_delay():
    """Skip real sleeping so retry tests run instantly."""
    with patch("tenacity.nap.time.sleep"):
        yield


@pytest.fixture(name="retry_settings", autouse=True)
def fixture_retry_settings(settings):
    """Give the fake TEST integration the same defaults as the real ones."""
    settings.TEST_RETRY_MAX_ATTEMPTS = 3
    settings.TEST_RETRY_WAIT_MULTIPLIER = 2
    settings.TEST_RETRY_WAIT_MIN = 2
    return settings


@pytest.fixture(name="base_request")
def fixture_base_request():
    """Mock the underlying requests.Session.request the retrying layer wraps."""
    with patch.object(requests.Session, "request") as mock_request:
        yield mock_request


def _session():
    return RetryingSession("TEST", logging.getLogger(LOGGER_NAME))


def _response(status_code):
    response = MagicMock()
    response.status_code = status_code
    return response


def test_get_is_retried_on_timeout(base_request):
    """A GET that times out is replayed and the later success is returned."""
    ok = _response(200)
    base_request.side_effect = [Timeout("read timed out"), ok]

    result = _session().get(URL, timeout=30)

    assert result is ok
    assert base_request.call_count == 2


def test_get_is_retried_on_connection_error(base_request):
    """A GET that hits a connection error is replayed."""
    ok = _response(200)
    base_request.side_effect = [RequestsConnectionError("reset"), ok]

    assert _session().get(URL) is ok
    assert base_request.call_count == 2


def test_get_is_retried_on_server_error_response(base_request):
    """A 5xx response is transient: the GET is replayed."""
    ok = _response(200)
    base_request.side_effect = [_response(504), ok]

    assert _session().get(URL) is ok
    assert base_request.call_count == 2


def test_get_returns_last_server_error_response_once_attempts_are_exhausted(
    base_request,
):
    """After the last attempt the 5xx response is handed back, so the caller's
    raise_for_status() still raises as it did without the retrying layer."""
    last = _response(503)
    base_request.side_effect = [_response(502), _response(504), last]

    assert _session().get(URL) is last
    assert base_request.call_count == 3


def test_get_reraises_timeout_once_attempts_are_exhausted(base_request):
    """A GET that keeps timing out raises the original Timeout."""
    base_request.side_effect = Timeout("read timed out")

    with pytest.raises(Timeout):
        _session().get(URL)

    assert base_request.call_count == 3


def test_get_is_not_retried_on_client_error_response(base_request):
    """A 4xx is permanent and must fail fast."""
    not_found = _response(404)
    base_request.return_value = not_found

    assert _session().get(URL) is not_found
    base_request.assert_called_once()


def test_post_is_not_retried_by_default(base_request):
    """A POST may have side effects, so it is never replayed unless asked."""
    base_request.side_effect = Timeout("read timed out")

    with pytest.raises(Timeout):
        _session().post(URL, json={})

    base_request.assert_called_once()


def test_post_flagged_idempotent_is_retried(base_request):
    """A read-only POST can opt in to retries with idempotent=True."""
    ok = _response(200)
    base_request.side_effect = [Timeout("read timed out"), ok]

    assert _session().post(URL, json={}, idempotent=True) is ok
    assert base_request.call_count == 2


def test_get_flagged_not_idempotent_is_not_retried(base_request):
    """A GET can opt out of retries, e.g. when its caller already retries."""
    base_request.side_effect = Timeout("read timed out")

    with pytest.raises(Timeout):
        _session().get(URL, idempotent=False)

    base_request.assert_called_once()


def test_idempotent_flag_is_not_forwarded_to_requests(base_request):
    """requests.Session.request does not know the idempotent keyword."""
    base_request.return_value = _response(200)

    _session().post(URL, json={"a": 1}, idempotent=True)

    _, kwargs = base_request.call_args
    assert "idempotent" not in kwargs
    assert kwargs["json"] == {"a": 1}


def test_max_attempts_is_read_at_call_time(base_request, retry_settings):
    """Overriding the setting after the session is built is honored."""
    session = _session()
    retry_settings.TEST_RETRY_MAX_ATTEMPTS = 5
    base_request.side_effect = Timeout("read timed out")

    with pytest.raises(Timeout):
        session.get(URL)

    assert base_request.call_count == 5


def test_each_retry_is_logged_at_info(base_request, caplog):
    """Every replay is announced at INFO, before sleeping."""
    base_request.side_effect = [Timeout("read timed out"), _response(200)]

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        _session().get(URL)

    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(infos) == 1
    assert "Retrying" in infos[0].getMessage()


def test_final_failure_is_logged_at_error_with_method_and_url(base_request, caplog):
    """Giving up is logged once at ERROR, naming the request that failed."""
    base_request.side_effect = Timeout("read timed out")

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(Timeout):
            _session().get(URL)

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    message = errors[0].getMessage()
    assert "GET" in message
    assert URL in message
    assert "3 attempt(s)" in message


def test_final_server_error_is_logged_at_error(base_request, caplog):
    """Exhausting attempts on 5xx responses is logged at ERROR too."""
    base_request.return_value = _response(504)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        _session().get(URL)

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "504" in errors[0].getMessage()


def test_query_string_is_not_logged(base_request, caplog):
    """A presigned URL carries its signature in the query string: it is kept out
    of the logged URL and of the logged error, which urllib3 fills with the URL."""
    base_request.side_effect = RequestsConnectionError(
        "Max retries exceeded with url: /items?X-Amz-Signature=secret"
    )

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(RequestsConnectionError):
            _session().put(f"{URL}?X-Amz-Signature=secret", data=b"x", idempotent=True)

    assert caplog.records
    for record in caplog.records:
        assert "secret" not in record.getMessage()
        assert URL in record.getMessage()


def test_get_is_retried_on_a_body_cut_short(base_request):
    """A connection dropped while reading the body is as transient as a timeout."""
    ok = _response(200)
    base_request.side_effect = [ChunkedEncodingError("IncompleteRead"), ok]

    assert _session().get(URL) is ok
    assert base_request.call_count == 2


@pytest.mark.parametrize(
    "error",
    [SSLError("EOF occurred in violation of protocol"), ProxyError("reset by peer")],
)
def test_transient_tls_and_proxy_errors_are_retried(base_request, error):
    """A TLS handshake cut by a load balancer or a restarting proxy is transient."""
    ok = _response(200)
    base_request.side_effect = [error, ok]

    assert _session().get(URL) is ok
    assert base_request.call_count == 2


def _certificate_error():
    """Build the SSLError requests raises when the certificate can't be verified."""
    reason = urllib3.exceptions.SSLError(
        ssl.SSLCertVerificationError("certificate verify failed")
    )
    return SSLError(urllib3.exceptions.MaxRetryError(None, "/items", reason=reason))


def test_certificate_errors_are_not_retried(base_request):
    """A certificate that can't be verified fails every attempt: raised at once."""
    base_request.side_effect = _certificate_error()

    with pytest.raises(SSLError):
        _session().get(URL)

    base_request.assert_called_once()


def test_limited_retries_caps_attempts_inside_the_block(base_request):
    """A web request that must answer quickly can cap the attempts of every call."""
    base_request.side_effect = Timeout("read timed out")

    with limited_retries(max_attempts=1), pytest.raises(Timeout):
        _session().get(URL)

    assert base_request.call_count == 1


def test_limited_retries_is_reset_after_the_block(base_request):
    """Calls made after the block get the configured attempts back."""
    base_request.side_effect = Timeout("read timed out")
    with limited_retries(max_attempts=1):
        pass

    with pytest.raises(Timeout):
        _session().get(URL)

    assert base_request.call_count == 3


def test_limited_retries_never_raises_the_configured_attempts(base_request):
    """The cap only lowers the attempts, it never adds some."""
    base_request.side_effect = Timeout("read timed out")

    with limited_retries(max_attempts=10), pytest.raises(Timeout):
        _session().get(URL)

    assert base_request.call_count == 3


def test_discarded_server_error_responses_are_closed(base_request, retry_settings):
    """A retried 5xx response is closed so its pooled connection is released;
    the response handed back to the caller is left open."""
    retry_settings.TEST_RETRY_MAX_ATTEMPTS = 2
    first, last = _response(502), _response(503)
    base_request.side_effect = [first, last]

    assert _session().get(URL) is last

    first.close.assert_called_once()
    last.close.assert_not_called()


def test_query_passed_as_params_is_not_logged(base_request, caplog):
    """urllib3 puts the prepared URL, params included, in its error message."""
    base_request.side_effect = RequestsConnectionError(
        "Max retries exceeded with url: /users/?q=jane%40example.com (Caused by X)"
    )

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(RequestsConnectionError):
            _session().get(URL, params={"q": "jane@example.com"})

    assert caplog.records
    for record in caplog.records:
        assert "jane" not in record.getMessage()


def test_query_is_not_logged_on_a_real_connection_error(caplog):
    """Same check against the error urllib3 actually raises, on a refused port."""
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(RequestsConnectionError):
            _session().put(
                "http://127.0.0.1:9/file?X-Amz-Signature=secret",
                data=b"x",
                timeout=1,
                idempotent=True,
            )

    assert caplog.records
    for record in caplog.records:
        assert "secret" not in record.getMessage()


def test_query_with_unencoded_quotes_is_fully_redacted(base_request, caplog):
    """requests leaves quotes and parentheses unencoded in a URL string: the
    redaction must not stop at them and leak the rest of the query."""
    base_request.side_effect = RequestsConnectionError(
        "Max retries exceeded with url: /users?search=O'Brien&token=abc (Caused by X)"
    )

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(RequestsConnectionError):
            _session().get(f"{URL}?search=O'Brien&token=abc")

    assert caplog.records
    for record in caplog.records:
        assert "Brien" not in record.getMessage()
        assert "token=abc" not in record.getMessage()
