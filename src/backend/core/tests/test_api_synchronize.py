"""Tests for the synchronize API view."""

import logging
import re
from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

import jwt
import pytest
import responses
from cryptography.fernet import Fernet
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout
from rest_framework.test import APIClient

from core import factories
from core.models import FeatureFlag, Workspace
from core.sources.resana.token_manager import ResanaTokenManager

pytestmark = pytest.mark.django_db

_FILESYSTEM = "core.sources.filesystem.FileSystemSourceBackend"
_RESANA = "core.sources.resana.backend.ResanaSourceBackend"


@pytest.fixture(autouse=True)
def _writable(settings, tmp_path):
    FeatureFlag.objects.create(name=FeatureFlag.Name.READ_ONLY_MODE, is_active=False)
    settings.SOURCE_BACKEND = _FILESYSTEM
    settings.FILESYSTEM_SOURCE_ROOT = str(tmp_path)
    settings.POSTHOG_KEY = "phc_test"
    settings.RESANA_RETRY_MAX_ATTEMPTS = 3
    settings.RESANA_WEB_ENDPOINT = "https://resana.example.com"
    settings.OIDC_TOKENS_ENCRYPTION_KEY = Fernet.generate_key().decode()


def _client(user=None):
    user = user or factories.UserFactory()
    client = APIClient()
    client.force_login(user)
    return client, user


def _connect(user):
    access = jwt.encode(
        {"exp": int((timezone.now() + timedelta(hours=2)).timestamp())},
        "test-secret-long-enough-for-hs256",
        algorithm="HS256",
    )
    ResanaTokenManager(user).store_tokens(
        offline_token="offline-token",
        access_token=access,
        session_id="sess",
        csrf_token="csrf",
    )


def test_synchronize_captures_workspaces_counts():
    """A sync emits workspaces_synchronized with counts as person properties."""
    client, user = _client()
    user.workspaces.add(
        factories.WorkspaceFactory(),
        factories.WorkspaceFactory(status=Workspace.Status.SUCCESS),
    )

    with patch("posthog.capture") as capture:
        response = client.get("/api/v1.0/synchronize/")

    assert response.status_code == 200
    assert capture.call_args.args[0] == "workspaces_synchronized"
    assert capture.call_args.kwargs["distinct_id"] == user.email
    assert capture.call_args.kwargs["properties"]["$set"] == {
        "workspaces_found": 2,
        "workspaces_not_migrated": 1,
    }


def test_synchronize_does_not_retry_api_calls(settings, caplog):
    """The sync runs inside the web request: a hanging source is tried once."""
    settings.SOURCE_BACKEND = _RESANA
    client, user = _client()
    _connect(user)

    def time_out(_request):
        raise Timeout("timed out")

    with responses.RequestsMock(assert_all_requests_are_fired=False) as recorded:
        recorded.add_callback(
            responses.POST, re.compile(r"https://"), callback=time_out
        )
        recorded.add_callback(responses.GET, re.compile(r"https://"), callback=time_out)
        with caplog.at_level(logging.ERROR):
            response = client.get("/api/v1.0/synchronize/")

    assert response.status_code == 403
    assert response.json()["error_name"] == "SourceUnavailable"
    assert "giving up after 1 attempt" in caplog.text
    assert "listerMesEspacesV2" in caplog.text


def test_synchronize_unreachable_source_raises_source_unavailable(settings, caplog):
    """A source that cannot be reached is reported, not turned into a server error."""
    settings.SOURCE_BACKEND = _RESANA
    client, user = _client()
    _connect(user)

    def down(_request):
        raise RequestsConnectionError("down")

    with responses.RequestsMock(assert_all_requests_are_fired=False) as recorded:
        recorded.add_callback(responses.POST, re.compile(r"https://"), callback=down)
        recorded.add_callback(responses.GET, re.compile(r"https://"), callback=down)
        with caplog.at_level(logging.ERROR):
            response = client.get("/api/v1.0/synchronize/")

    assert response.status_code == 403
    assert response.json()["error_name"] == "SourceUnavailable"
    assert "giving up after 1 attempt" in caplog.text
