"""Tests for the synchronize API view."""

from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from core import factories
from core.models import FeatureFlag, Workspace

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
