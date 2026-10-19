"""Tests for the RetryingSession the Drive backend sends its HTTP calls through."""

# pylint: disable=protected-access

import ssl
import uuid
from datetime import timedelta
from unittest.mock import MagicMock, mock_open, patch

from django.utils import timezone

import pytest
import requests
import urllib3
from requests.exceptions import ChunkedEncodingError, HTTPError, SSLError, Timeout

from core.destinations.drive.drive_backend import DriveServiceAccountBackend
from core.retrying_session import RetryingSession
from core.tests.destinations.drive.drive_http_mock import patch_drive_http

FAKE_ITEM_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def no_retry_delay():
    """Skip real sleeping so retry tests run instantly."""
    with (
        patch("tenacity.nap.time.sleep"),
        patch("core.destinations.drive.drive_backend.time.sleep"),
    ):
        yield


@pytest.fixture(name="backend")
def fixture_backend(settings):
    """A service account backend holding a valid token, so no refresh is sent."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"
    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"
    backend._token_expires_at = timezone.now() + timedelta(hours=1)
    return backend


def _response(status_code=200, payload=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload or {}
    if status_code >= 400:
        response.raise_for_status.side_effect = HTTPError(response=response)
    return response


def test_backend_sends_its_calls_through_a_retrying_session():
    """One RetryingSession per backend, driven by the DRIVE_RETRY_* settings,
    so Drive connections are reused across calls."""
    backend = DriveServiceAccountBackend()

    assert isinstance(backend.session, RetryingSession)
    assert backend.session.settings_prefix == "DRIVE"


def test_drive_token_is_not_set_on_the_session(backend):
    """The token is passed per call: a session-wide Authorization header would
    also be sent to the S3 presigned upload URL."""
    with patch_drive_http() as http:
        http.get.return_value = _response(payload={"id": "item"})
        backend.get_item("item")

    assert "Authorization" not in backend.session.headers
    assert http.get.call_args.kwargs["headers"] == {"Authorization": "Bearer tok"}


def test_upload_to_s3_does_not_send_the_drive_token(backend):
    """The S3 presigned URL is signed on its own and must never receive the token."""
    with (
        patch_drive_http() as http,
        patch("builtins.open", mock_open(read_data=b"binary content")),
    ):
        http.put.return_value = _response()
        backend.upload_to_s3("https://s3.example.com/file?sig=x", "/tmp/doc.pdf")

    assert "headers" not in http.put.call_args.kwargs
    assert "auth" not in http.put.call_args.kwargs


def test_list_children_retries_only_the_failing_page(backend):
    """A 5xx on the second page replays that page, not the whole listing."""
    page_2_url = "https://drive.example.com/external_api/v1.0/items/f/children/?page=2"
    with patch_drive_http() as http:
        http.get.side_effect = [
            _response(payload={"results": [{"id": "a"}], "next": page_2_url}),
            _response(status_code=502),
            _response(payload={"results": [{"id": "b"}], "next": None}),
        ]
        children = backend.list_children("f")

    assert [child["id"] for child in children] == ["a", "b"]
    urls = [call.args[0] for call in http.get.call_args_list]
    assert urls[1:] == [page_2_url, page_2_url]


def test_create_item_existence_check_is_not_retried_twice_over(backend, settings):
    """The create loop already retries; its existence check must not multiply
    the attempts by also being retried by the session."""
    settings.DRIVE_RETRY_MAX_ATTEMPTS = 3
    with (
        patch(
            "core.destinations.drive.drive_backend.uuid.uuid4",
            return_value=uuid.UUID(FAKE_ITEM_ID),
        ),
        patch_drive_http() as http,
        pytest.raises(Timeout),
    ):
        http.post.side_effect = Timeout("read timed out")
        http.get.return_value = _response(status_code=503)
        backend.create_folder("docs")

    assert http.post.call_count == 3
    assert http.get.call_count == 3


def test_delete_item_is_retried_by_the_session(backend):
    """Deleting a pending orphan is safe to replay on a transient failure."""
    with patch_drive_http() as http:
        http.delete.side_effect = [
            Timeout("read timed out"),
            _response(status_code=204),
        ]
        backend._delete_item("item")

    assert http.delete.call_count == 2


def test_notify_upload_ended_is_not_retried_by_the_session(backend, settings):
    """upload-ended keeps its own loop (it handles the "already processed" 400):
    the session must not add retries on top of it."""
    settings.DRIVE_RETRY_MAX_ATTEMPTS = 2
    with patch_drive_http() as http, pytest.raises(Timeout):
        http.post.side_effect = Timeout("read timed out")
        backend.notify_upload_ended("item")

    assert http.post.call_count == 2


def _refresh_to(backend, token):
    def refresh():
        backend._access_token = token
        backend._token_expires_at = timezone.now() + timedelta(hours=1)

    return refresh


def _expire_token_and_time_out(backend):
    """Let the token expire before timing out, as a call outlasting its lifetime."""
    backend._token_expires_at = timezone.now() - timedelta(seconds=1)
    return Timeout("read timed out")


def test_each_attempt_gets_a_fresh_token(backend):
    """A retry after a long timeout must not reuse a token that expired meanwhile:
    the token is read again when each attempt is prepared."""
    responses = iter([None, _response(payload={"id": "i"})])

    def get(*_args, **_kwargs):
        response = next(responses)
        if response is None:
            raise _expire_token_and_time_out(backend)
        return response

    with (
        patch.object(backend, "_refresh", side_effect=_refresh_to(backend, "tok-2")),
        patch_drive_http() as http,
    ):
        http.get.side_effect = get
        backend.get_item("i")

    tokens = [
        call.kwargs["headers"]["Authorization"] for call in http.get.call_args_list
    ]
    assert tokens == ["Bearer tok", "Bearer tok-2"]


def test_each_prepared_attempt_carries_a_fresh_token(backend):
    """Same check below requests' own request preparation, at the adapter level."""
    sent = []

    def send(prepared, **_kwargs):
        sent.append(prepared.headers["Authorization"])
        if len(sent) == 1:
            raise _expire_token_and_time_out(backend)
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"id": "i"}'
        response.request = prepared
        response.url = prepared.url
        return response

    with (
        patch.object(backend, "_refresh", side_effect=_refresh_to(backend, "tok-2")),
        patch.object(requests.adapters.HTTPAdapter, "send", side_effect=send),
    ):
        assert backend.get_item("i") == {"id": "i"}

    assert sent == ["Bearer tok", "Bearer tok-2"]


@pytest.mark.parametrize(
    "call",
    [
        lambda backend: backend.share_with_user("item", "user-id"),
        lambda backend: backend.invite_by_email("item", "jane@example.com"),
    ],
    ids=["share_with_user", "invite_by_email"],
)
def test_sharing_posts_are_retried_on_server_error(backend, call):
    """A duplicate grant or invitation is accepted, so a 5xx is replayed."""
    with patch_drive_http() as http:
        http.post.side_effect = [_response(status_code=502), _response(status_code=201)]
        call(backend)

    assert http.post.call_count == 2


def test_get_item_is_retried_on_server_error(backend):
    """A 5xx on the item lookup is replayed by the session."""
    with patch_drive_http() as http:
        http.get.side_effect = [
            _response(status_code=503),
            _response(payload={"id": "i"}),
        ]
        assert backend.get_item("i") == {"id": "i"}

    assert http.get.call_count == 2


@pytest.fixture(name="fixed_item_id")
def fixture_fixed_item_id():
    """Make the client-generated item id deterministic."""
    with patch(
        "core.destinations.drive.drive_backend.uuid.uuid4",
        return_value=uuid.UUID(FAKE_ITEM_ID),
    ):
        yield


@pytest.mark.usefixtures("fixed_item_id")
def test_create_item_is_retried_when_the_response_is_cut_short(backend):
    """A creation answer cut mid-body is ambiguous like a timeout: the item is
    looked up by id, then the POST is replayed since it was not created."""
    with patch_drive_http() as http:
        http.post.side_effect = [
            ChunkedEncodingError("IncompleteRead"),
            _response(status_code=201, payload={"id": FAKE_ITEM_ID}),
        ]
        http.get.return_value = _response(status_code=404)
        assert backend.create_folder("docs") == {"id": FAKE_ITEM_ID}

    assert http.post.call_count == 2
    http.get.assert_called_once()


def _certificate_error():
    """Build the SSLError requests raises when the certificate can't be verified."""
    reason = urllib3.exceptions.SSLError(
        ssl.SSLCertVerificationError("certificate verify failed")
    )
    return SSLError(urllib3.exceptions.MaxRetryError(None, "/x", reason=reason))


@pytest.mark.usefixtures("fixed_item_id")
def test_create_item_is_not_retried_on_certificate_error(backend):
    """A certificate that can't be verified fails every attempt: raised right
    away, without looking the item up."""
    with patch_drive_http() as http, pytest.raises(SSLError):
        http.post.side_effect = _certificate_error()
        backend.create_folder("docs")

    http.post.assert_called_once()
    http.get.assert_not_called()


def test_notify_upload_ended_is_retried_when_the_response_is_cut_short(backend):
    """upload-ended's own loop replays a response cut mid-body like a timeout."""
    with patch_drive_http() as http:
        http.post.side_effect = [
            ChunkedEncodingError("IncompleteRead"),
            _response(status_code=200),
        ]
        backend.notify_upload_ended("item")

    assert http.post.call_count == 2
