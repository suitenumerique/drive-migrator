"""Tests for the PostHog analytics helper."""

from unittest.mock import patch

from django.test import override_settings

import pytest

from core import factories
from core.analytics import posthog_capture, workspaces_counts
from core.models import Workspace

pytestmark = pytest.mark.django_db


@override_settings(POSTHOG_KEY=None)
@patch("core.analytics.posthog.capture")
def test_posthog_capture_noop_without_key(capture):
    posthog_capture("event", factories.UserFactory(), {"a": 1})
    capture.assert_not_called()


@override_settings(POSTHOG_KEY="key")
@patch("core.analytics.posthog.capture")
def test_posthog_capture_with_workspace(capture):
    user = factories.UserFactory(email="user@example.com")
    workspace = factories.WorkspaceFactory(
        destination_statuses={"drive": Workspace.Status.PENDING}
    )
    posthog_capture("event", user, {"a": 1}, workspace=workspace)
    capture.assert_called_once_with(
        "event",
        distinct_id="user@example.com",
        properties={
            "a": 1,
            "workspace_id": str(workspace.id),
            "destinations": ["drive"],
        },
    )


def test_workspaces_counts():
    user = factories.UserFactory()
    user.workspaces.add(
        factories.WorkspaceFactory(),
        factories.WorkspaceFactory(status=Workspace.Status.SUCCESS),
        factories.WorkspaceFactory(status=Workspace.Status.FAILURE),
    )
    assert workspaces_counts(user) == {
        "workspaces_found": 3,
        "workspaces_not_migrated": 1,
    }
