"""Test helper intercepting the HTTP calls the Drive backend sends through its session."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from requests.exceptions import HTTPError

# Defaults requests' get/post/... helpers add before calling request(); dropped
# so assertions can keep describing the call as the backend wrote it.
_SESSION_DEFAULT_KWARGS = {
    "allow_redirects": True,
    "params": None,
    "data": None,
    "json": None,
}


def _apply_auth(kwargs: dict) -> dict:
    """Turn an auth= callable into the headers it sets, as requests does when it
    prepares each attempt, so assertions keep checking the Authorization header."""
    auth = kwargs.pop("auth", None)
    if auth is None:
        return kwargs
    prepared = SimpleNamespace(headers=dict(kwargs.get("headers") or {}))
    auth(prepared)
    return {**kwargs, "headers": prepared.headers}


def _pop_request_id(kwargs: dict) -> tuple[str, dict]:
    """Take out the X-Request-ID header the session adds to every call, so
    assertions keep describing the call as the backend wrote it."""
    headers = dict(kwargs.get("headers") or {})
    request_id = headers.pop("X-Request-ID")
    kwargs = {key: value for key, value in kwargs.items() if key != "headers"}
    return request_id, {**kwargs, "headers": headers} if headers else kwargs


def _align_status_code(response) -> None:
    """Give a bare MagicMock response the status code its raise_for_status implies,
    since RetryingSession decides whether to retry from status_code."""
    if isinstance(getattr(response, "status_code", None), int):
        return
    error = response.raise_for_status.side_effect
    error_response = (
        getattr(error, "response", None) if isinstance(error, HTTPError) else None
    )
    status_code = getattr(error_response, "status_code", None)
    response.status_code = status_code if isinstance(status_code, int) else 200


@contextmanager
def patch_drive_http():
    """Patch the requests.Session.request wrapped by the backend's RetryingSession.

    Yields a mock whose get/post/put/delete attributes receive each call, so tests
    set side effects and assert calls on them as on the requests module functions,
    while the session's retries stay in play. The request id of each call goes to
    its request_ids list instead.
    """
    http = MagicMock()
    http.request_ids = []

    def dispatch(method, url, **kwargs):
        request_id, kwargs = _pop_request_id(kwargs)
        http.request_ids.append(request_id)
        kwargs = {
            key: value
            for key, value in kwargs.items()
            if key not in _SESSION_DEFAULT_KWARGS
            or value != _SESSION_DEFAULT_KWARGS[key]
        }
        response = getattr(http, method.lower())(url, **_apply_auth(kwargs))
        _align_status_code(response)
        return response

    with patch.object(requests.Session, "request", side_effect=dispatch):
        yield http
