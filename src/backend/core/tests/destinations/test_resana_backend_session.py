"""Tests for the RetryingSession ResanaBackend sends its Resana API calls through."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from core.destinations.resana.resana_backend import ResanaBackend
from core.retrying_session import RetryingSession

API = "https://resana-api.example.com"


@pytest.fixture(autouse=True)
def resana_settings(settings):
    """Point the backend at fake Resana endpoints."""
    settings.RESANA_API_ENDPOINT = API
    settings.RESANA_AUTH_ENDPOINT = "https://resana-auth.example.com/login"


@pytest.fixture(name="base_request")
def fixture_base_request():
    """Mock the requests.Session.request wrapped by RetryingSession, without sleeping."""
    with (
        patch("tenacity.nap.time.sleep"),
        patch.object(requests.Session, "request") as mock_request,
    ):
        yield mock_request


def _response(status_code=200, payload=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload or {}
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
    return response


def _logged_in_backend(base_request):
    """Return a backend whose login already went through, with the mock reset."""
    base_request.return_value = _response(302)
    backend = ResanaBackend()
    backend.init_jwt()
    # The mocked login sets no interstis_access cookie: fake the token it carries.
    backend.jwt = "tok"
    base_request.reset_mock(return_value=True)
    return backend


def test_create_jwt_builds_a_retrying_session_with_resana_settings(base_request):
    """Every Resana API call goes through a RetryingSession driven by RESANA_RETRY_*."""
    backend = _logged_in_backend(base_request)

    assert isinstance(backend.session, RetryingSession)
    assert backend.session.settings_prefix == "RESANA"


def test_login_is_retried_on_timeout(base_request):
    """Logging in again only opens another session, so a timed out login is replayed."""
    base_request.side_effect = [requests.Timeout("read timed out"), _response(302)]

    ResanaBackend().init_jwt()

    assert base_request.call_count == 2
    assert base_request.call_args.kwargs["timeout"] == 30


def test_get_is_retried_on_server_error(base_request):
    """A 5xx on a read is transient: the GET is replayed."""
    backend = _logged_in_backend(base_request)
    base_request.side_effect = [
        _response(503),
        _response(payload={"status": "running"}),
    ]

    response = backend.request("get", "/jobs/job-id")

    assert response.json() == {"status": "running"}
    assert base_request.call_count == 2


def test_post_is_not_retried(base_request):
    """Workspace or job creation would be duplicated if replayed."""
    backend = _logged_in_backend(base_request)
    base_request.side_effect = requests.Timeout("read timed out")

    with pytest.raises(requests.Timeout):
        backend.request("post", "/workspaces", json={"name": "ws"})

    base_request.assert_called_once()


def test_request_has_a_default_timeout(base_request):
    """Without a timeout a stuck call hangs forever and is never retried."""
    backend = _logged_in_backend(base_request)
    base_request.return_value = _response()

    backend.request("get", "/organizations")

    assert base_request.call_args.kwargs["timeout"] == 30


def test_request_keeps_an_explicit_timeout(base_request):
    """A caller-provided timeout wins over the default."""
    backend = _logged_in_backend(base_request)
    base_request.return_value = _response()

    backend.request("get", "/organizations", timeout=120)

    assert base_request.call_args.kwargs["timeout"] == 120


def test_post_waits_longer_for_the_response(base_request):
    """A creation POST is not retried: timing out while Resana still completes it
    would leave an unrecorded workspace or job, so it waits up to 5 minutes."""
    backend = _logged_in_backend(base_request)
    base_request.return_value = _response()

    backend.request("post", "/jobs", json={})

    assert base_request.call_args.kwargs["timeout"] == (10, 300)
