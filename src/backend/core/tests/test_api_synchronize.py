"""Tests for the synchronize API view."""

import logging
from unittest.mock import patch

import pytest
import requests
from rest_framework.test import APIClient

from core import factories
from core.models import FeatureFlag, Workspace
from core.retrying_session import RetryingSession

pytestmark = pytest.mark.django_db


def test_synchronize_captures_workspaces_counts():
    """A sync emits workspaces_synchronized with counts as person properties."""
    FeatureFlag.objects.create(name=FeatureFlag.Name.READ_ONLY_MODE, is_active=False)
    user = factories.UserFactory()
    user.workspaces.add(
        factories.WorkspaceFactory(),
        factories.WorkspaceFactory(status=Workspace.Status.SUCCESS),
    )
    client = APIClient()
    client.force_login(user)

    with (
        patch("core.api.views.synchronize.SourceManager.synchronize"),
        patch("core.api.views.synchronize.posthog_capture") as capture,
    ):
        response = client.get("/api/v1.0/synchronize/")

    assert response.status_code == 200
    capture.assert_called_once_with(
        "workspaces_synchronized",
        user,
        {"$set": {"workspaces_found": 2, "workspaces_not_migrated": 1}},
    )


def test_synchronize_does_not_retry_api_calls(settings):
    """The sync runs inside the web request: retrying a hanging source would
    outlast the server's timeout, so every API call gets a single attempt."""
    settings.RESANA_RETRY_MAX_ATTEMPTS = 3
    FeatureFlag.objects.create(name=FeatureFlag.Name.READ_ONLY_MODE, is_active=False)
    client = APIClient()
    client.force_login(factories.UserFactory())

    def synchronize(_user):
        session = RetryingSession("RESANA", logging.getLogger(__name__))
        with pytest.raises(requests.Timeout):
            session.get("https://source.example.com/workspaces")

    with (
        patch("tenacity.nap.time.sleep"),
        patch.object(
            requests.Session, "request", side_effect=requests.Timeout("timed out")
        ) as base_request,
        patch(
            "core.api.views.synchronize.SourceManager.synchronize",
            side_effect=synchronize,
        ),
        patch("core.api.views.synchronize.posthog_capture"),
    ):
        response = client.get("/api/v1.0/synchronize/")

    assert response.status_code == 200
    assert base_request.call_count == 1


def test_synchronize_unreachable_source_raises_source_unavailable():
    """A source that cannot be reached is reported, not turned into a server error."""
    FeatureFlag.objects.create(name=FeatureFlag.Name.READ_ONLY_MODE, is_active=False)
    client = APIClient()
    client.force_login(factories.UserFactory())

    with patch(
        "core.api.views.synchronize.SourceManager.synchronize",
        side_effect=requests.ConnectionError(),
    ):
        response = client.get("/api/v1.0/synchronize/")

    assert response.json()["error_name"] == "SourceUnavailable"
