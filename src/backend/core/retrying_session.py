"""requests.Session that retries transient failures of requests."""

import re
import ssl
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import urlsplit

from django.conf import settings

import requests
from requests.exceptions import ChunkedEncodingError, SSLError, Timeout
from requests.exceptions import ConnectionError as RequestsConnectionError
from tenacity import (
    Retrying,
    retry_if_exception,
    retry_if_result,
    stop_after_attempt,
    wait_exponential,
)

# A query string in an error message, e.g. urllib3's "Max retries exceeded with
# url: /path?query", which holds the prepared URL, params= included.
# Runs up to the next whitespace: requests leaves quotes and parentheses
# unencoded in a URL string, so stopping at them could leak the rest.
_QUERY_IN_TEXT_RE = re.compile(r"\?\S*")

_max_attempts_cap: ContextVar[int | None] = ContextVar(
    "retrying_session_max_attempts_cap", default=None
)


@contextmanager
def limited_retries(max_attempts: int):
    """Cap the attempts of every RetryingSession call made inside the block, e.g.
    in a web request that must answer before the server's timeout."""
    token = _max_attempts_cap.set(max_attempts)
    try:
        yield
    finally:
        _max_attempts_cap.reset(token)


def _is_caused_by(error: BaseException, error_type: type) -> bool:
    """Look for error_type in error's chain, following causes, contexts and the
    wrapped exceptions requests and urllib3 keep in args or `reason`."""
    seen = set()
    pending = [error]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, error_type):
            return True
        linked = (
            current.__cause__,
            current.__context__,
            getattr(current, "reason", None),
            *current.args,
        )
        pending.extend(item for item in linked if isinstance(item, BaseException))
    return False


def is_transient_error(error: BaseException) -> bool:
    """Timeouts, dropped connections (TLS and proxy ones included) and bodies cut
    short are worth retrying. A certificate that can't be verified is not: it
    would fail every attempt."""
    if isinstance(error, SSLError) and _is_caused_by(
        error, ssl.SSLCertVerificationError
    ):
        return False
    return isinstance(error, (Timeout, RequestsConnectionError, ChunkedEncodingError))


def _is_server_error(response) -> bool:
    """A 5xx response is a transient failure on the remote side, safe to retry."""
    return response.status_code >= 500


def _strip_query(url: str) -> str:
    """Drop the query string, which may carry credentials (presigned URLs)."""
    return urlsplit(url)._replace(query="", fragment="").geturl()


def _describe_failure(outcome) -> str:
    if not outcome.failed:
        return f"HTTP {outcome.result().status_code}"
    return _QUERY_IN_TEXT_RE.sub("?<redacted>", repr(outcome.exception()))


class RetryingSession(requests.Session):
    """Session retrying timeouts, connection errors and 5xx responses.

    Only safe methods (GET, HEAD, OPTIONS) are retried by default, since
    replaying them can't produce a duplicate side effect. A call opts in or out
    with `retry=True/False`, e.g. a read-only POST. Attempts and backoff
    come from the `<settings_prefix>_RETRY_*` settings, read at call time.
    Once attempts are exhausted the last exception is reraised, or the last 5xx
    response returned so the caller's raise_for_status() behaves as before.

    Errors raised while streaming a response body (stream=True) happen after
    request() returns, so they are not covered here.
    """

    SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

    def __init__(self, settings_prefix: str, logger):
        super().__init__()
        self.settings_prefix = settings_prefix
        self.logger = logger

    def _setting(self, name: str) -> int:
        return getattr(settings, f"{self.settings_prefix}_RETRY_{name}")

    def _max_attempts(self) -> int:
        configured = self._setting("MAX_ATTEMPTS")
        cap = _max_attempts_cap.get()
        return configured if cap is None else min(configured, cap)

    # pylint: disable-next=arguments-differ
    def request(self, method, url, *args, retry=None, **kwargs):
        if retry is None:
            retry = method.upper() in self.SAFE_METHODS
        if not retry:
            return super().request(method, url, *args, **kwargs)

        retrying = Retrying(
            retry=retry_if_exception(is_transient_error)
            | retry_if_result(_is_server_error),
            stop=stop_after_attempt(self._max_attempts()),
            wait=wait_exponential(
                multiplier=self._setting("WAIT_MULTIPLIER"),
                min=self._setting("WAIT_MIN"),
            ),
            before_sleep=self._log_retry(method, url),
            retry_error_callback=self._give_up(method, url),
        )
        return retrying(super().request, method, url, *args, **kwargs)

    def _log_retry(self, method, url):
        def _before_sleep(retry_state):
            outcome = retry_state.outcome
            if not outcome.failed:
                # Release the discarded 5xx response's pooled connection.
                outcome.result().close()
            self.logger.info(
                "Retrying %s %s in %s seconds after attempt %s failed: %s",
                method.upper(),
                _strip_query(url),
                retry_state.next_action.sleep,
                retry_state.attempt_number,
                _describe_failure(outcome),
            )

        return _before_sleep

    def _give_up(self, method, url):
        def _callback(retry_state):
            outcome = retry_state.outcome
            self.logger.error(
                "%s %s giving up after %s attempt(s): %s",
                method.upper(),
                _strip_query(url),
                retry_state.attempt_number,
                _describe_failure(outcome),
            )
            if outcome.failed:
                raise outcome.exception()
            return outcome.result()

        return _callback
