"""Tests for WorkspacesViewset — auth and download_archive."""

from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from core import factories

pytestmark = pytest.mark.django_db


def test_list_workspaces_anonymous():
    """Anonymous users must not access the workspaces list."""
    client = APIClient()
    response = client.get("/api/v1.0/workspaces/")
    assert response.status_code == 401


def test_download_archive_anonymous():
    """Anonymous users must not be able to request a download URL."""
    workspace = factories.WorkspaceFactory()
    client = APIClient()
    response = client.get(f"/api/v1.0/workspaces/{workspace.id}/download_archive/")
    assert response.status_code == 401


def test_download_archive_other_user_workspace_not_found():
    """A workspace not owned by the requesting user must not be accessible."""
    user = factories.UserFactory()
    other_workspace = factories.WorkspaceFactory()
    client = APIClient()
    client.force_login(user)

    response = client.get(
        f"/api/v1.0/workspaces/{other_workspace.id}/download_archive/"
    )
    assert response.status_code == 404


def test_download_archive_authenticated_owner():
    """The owning authenticated user gets a presigned download URL."""
    user = factories.UserFactory()
    workspace = factories.WorkspaceFactory()
    user.workspaces.add(workspace)
    client = APIClient()
    client.force_login(user)

    with patch(
        "core.api.views.workspaces.ArchiveManager.get_download_url"
    ) as mock_get_url:
        mock_get_url.return_value = "http://s3.example.com/ws.zip?token=abc"
        response = client.get(f"/api/v1.0/workspaces/{workspace.id}/download_archive/")

    assert response.status_code == 200
    assert response.json() == {"url": "http://s3.example.com/ws.zip?token=abc"}
