"""Tests for the upload-broker client of the tribu destination."""

import hashlib
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.utils import timezone

import pytest
import responses
from cryptography.fernet import Fernet
from freezegun import freeze_time
from requests.exceptions import HTTPError

from core.destinations.drive import drive_backend
from core.destinations.tribu.upload_broker import (
    UploadBrokerClient,
    UploadBrokerTokenBackend,
)
from core.encryption import encrypt_token

BROKER = "http://upload-broker.test"
OWNER = {"email": "owner@example.com", "first_name": "Ada", "last_name": "Lovelace"}
DATA = b"abcdefghij"


@pytest.fixture(autouse=True)
def broker_settings(settings):
    settings.OIDC_TOKENS_ENCRYPTION_KEY = Fernet.generate_key().decode()
    settings.MENSHEN_SERVER_ROOT_URL = "https://menshen.example.com"
    settings.MENSHEN_CLIENT_ID = "menshen-client"
    settings.MENSHEN_CLIENT_SECRET = "menshen-secret"
    settings.UPLOAD_BROKER_URL = BROKER
    settings.UPLOAD_BROKER_AUDIENCE = "upload-broker"
    settings.UPLOAD_BROKER_SCOPE = "upload-broker:archive:create"


@pytest.fixture(name="exchanged_token")
def fixture_exchanged_token():
    """Skip the Menshen exchange and use a fixed token."""
    with patch.object(
        UploadBrokerTokenBackend, "get_token", return_value="exchanged-tok"
    ):
        yield


@pytest.fixture(name="archive")
def fixture_archive(tmp_path):
    path = tmp_path / "workspace.zip"
    path.write_bytes(DATA)
    return str(path)


def _open_response(part_size=4):
    count = -(-len(DATA) // part_size)
    return {
        "ticket": "ticket-1",
        "part_size": part_size,
        "expires_at": "2026-11-04T00:00:00Z",
        "parts": [
            {"number": n, "url": f"http://s3.test/part/{n}?X-Amz-Signature=sig"}
            for n in range(1, count + 1)
        ],
    }


def _mock_broker(complete_status=204, complete_body=None, part_status=200):
    responses.post(f"{BROKER}/uploads", json=_open_response(), status=201)
    for number in (1, 2, 3):
        responses.put(
            f"http://s3.test/part/{number}",
            status=part_status,
            headers={"ETag": f'"etag-{number}"'},
        )

    responses.post(
        f"{BROKER}/uploads/complete", status=complete_status, json=complete_body
    )


def _calls(method, prefix):
    return [
        call
        for call in responses.calls
        if call.request.method == method and call.request.url.startswith(prefix)
    ]


def _upload(archive):
    user = MagicMock(oidc_access_token="", oidc_token_expires_at=None)
    UploadBrokerClient(user).upload(
        archive, source_id="ws-source-1", title="Workspace", owner=OWNER
    )


@responses.activate
@pytest.mark.usefixtures("exchanged_token")
def test_upload_announces_the_archive(archive):
    """The upload starts with the size, MD5, title and owner.

    It uses the exchanged token.
    """
    _mock_broker()
    _upload(archive)

    opened = _calls("POST", f"{BROKER}/uploads")[0]
    assert opened.request.url == f"{BROKER}/uploads"
    assert json.loads(opened.request.body) == {
        "source_id": "ws-source-1",
        "size": len(DATA),
        "md5": hashlib.md5(DATA).hexdigest(),
        "title": "Workspace",
        "owner": OWNER,
    }
    assert opened.request.headers["Authorization"] == "Bearer exchanged-tok"


@responses.activate
@pytest.mark.usefixtures("exchanged_token")
def test_upload_sends_each_part_to_its_url(archive):
    """Each part is sent to its presigned URL, without the bearer token."""
    _mock_broker()
    _upload(archive)

    puts = _calls("PUT", "http://s3.test/")
    assert [call.request.body for call in puts] == [b"abcd", b"efgh", b"ij"]
    assert all("Authorization" not in call.request.headers for call in puts)


@responses.activate
@pytest.mark.usefixtures("exchanged_token")
def test_upload_completes_with_the_part_etags(archive):
    """The complete call sends the ticket and the ETag of each part."""
    _mock_broker()
    _upload(archive)

    [complete] = _calls("POST", f"{BROKER}/uploads/complete")
    assert json.loads(complete.request.body) == {
        "ticket": "ticket-1",
        "parts": [
            {"number": 1, "etag": '"etag-1"'},
            {"number": 2, "etag": '"etag-2"'},
            {"number": 3, "etag": '"etag-3"'},
        ],
    }
    assert complete.request.headers["Authorization"] == "Bearer exchanged-tok"


@responses.activate
@pytest.mark.usefixtures("exchanged_token")
def test_already_queued_archive_is_a_success(archive):
    """If the archive is already in the Tribu queue, the upload succeeds."""
    _mock_broker(complete_status=409, complete_body={"detail": "already_queued"})
    _upload(archive)


@responses.activate
@pytest.mark.parametrize(
    ("status", "body"),
    [
        (409, {"detail": "parts_mismatch"}),
        (502, {"detail": "tribu_rejected"}),
        (503, {"detail": "tribu_unavailable"}),
        (409, None),
        (409, ["already_queued"]),
    ],
)
@pytest.mark.usefixtures("exchanged_token")
def test_completion_errors_fail_the_upload(archive, status, body):
    """Any other error on the complete call fails the upload."""
    _mock_broker(complete_status=status, complete_body=body)
    with pytest.raises(HTTPError):
        _upload(archive)


@responses.activate
@pytest.mark.usefixtures("exchanged_token")
def test_broker_url_with_trailing_slash(archive, settings):
    """A trailing slash in UPLOAD_BROKER_URL is ignored."""
    settings.UPLOAD_BROKER_URL = f"{BROKER}/"
    _mock_broker()
    _upload(archive)


@responses.activate
@pytest.mark.usefixtures("exchanged_token")
def test_refused_part_stops_the_upload(archive):
    """If the bucket rejects a part, the upload fails before the complete call."""
    _mock_broker(part_status=403)
    with pytest.raises(HTTPError):
        _upload(archive)

    assert not _calls("POST", f"{BROKER}/uploads/complete")


@pytest.fixture(name="menshen")
def fixture_menshen():
    """Replace the Menshen client; exchanges return a 5-minute token."""
    client = MagicMock()
    client.exchange.return_value = MagicMock(
        access_token="exchanged-tok", expires_in=300
    )
    with patch.object(drive_backend, "_get_menshen_client", return_value=client):
        yield client


def _user_with_proconnect_token():
    user = MagicMock()
    user.oidc_access_token = encrypt_token("user-tok")
    user.oidc_refresh_token = encrypt_token("refresh-tok")
    user.oidc_token_expires_at = timezone.now() + timedelta(hours=1)
    return user


def test_token_is_exchanged_for_the_broker_audience(menshen):
    """The user token is exchanged for an upload-broker token."""
    token = UploadBrokerTokenBackend(_user_with_proconnect_token()).get_token()

    request = menshen.exchange.call_args.args[0]
    assert request.subject_token == "user-tok"
    assert request.audience == "upload-broker"
    assert request.scope == "upload-broker:archive:create"
    assert token == "exchanged-tok"


def test_token_is_exchanged_again_when_it_expires(menshen):
    """The token is reused while valid, and exchanged again once expired."""
    tokens = UploadBrokerTokenBackend(_user_with_proconnect_token())
    with freeze_time() as frozen:
        tokens.get_token()
        tokens.get_token()
        assert menshen.exchange.call_count == 1

        frozen.tick(timedelta(seconds=300))
        tokens.get_token()
        assert menshen.exchange.call_count == 2
