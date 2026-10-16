"""Tests for WorkspacesViewset: auth, download_archive and integrity."""

from unittest.mock import patch

import pytest
from django_celery_results.models import TaskResult
from rest_framework.test import APIClient

from core import factories
from core.models import ExtraTaskInfo

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


# ---------------------------------------------------------------------------
# download_rejected_files
# ---------------------------------------------------------------------------


def _rejected_files_url(workspace, destination="drive"):
    return (
        f"/api/v1.0/workspaces/{workspace.id}/download_rejected_files/"
        f"?destination={destination}"
    )


def _owner_client(workspace):
    user = factories.UserFactory()
    user.workspaces.add(workspace)
    client = APIClient()
    client.force_login(user)
    return client


def test_download_rejected_files_anonymous():
    """Anonymous users must not be able to request a download URL."""
    workspace = factories.WorkspaceFactory()

    response = APIClient().get(_rejected_files_url(workspace))

    assert response.status_code == 401


def test_download_rejected_files_other_user_workspace_not_found():
    """A workspace not owned by the requesting user must not be accessible."""
    user = factories.UserFactory()
    other_workspace = factories.WorkspaceFactory(
        destination_metadata={"drive": {"rejected_files_s3_key": "ws.zip"}}
    )
    client = APIClient()
    client.force_login(user)

    response = client.get(_rejected_files_url(other_workspace))

    assert response.status_code == 404


@pytest.mark.parametrize("query", ["", "?destination=", "?destination=unknown"])
def test_download_rejected_files_requires_a_known_destination(query):
    """The destination whose refused files are wanted must be registered."""
    workspace = factories.WorkspaceFactory()

    response = _owner_client(workspace).get(
        f"/api/v1.0/workspaces/{workspace.id}/download_rejected_files/{query}"
    )

    assert response.status_code == 400


def test_download_rejected_files_not_found_without_zip():
    """No URL when the destination refused nothing in the last run."""
    workspace = factories.WorkspaceFactory(destination_metadata={"drive": {}})

    response = _owner_client(workspace).get(_rejected_files_url(workspace))

    assert response.status_code == 404


def test_download_rejected_files_authenticated_owner():
    """The owner gets a presigned URL of the destination's zip."""
    workspace = factories.WorkspaceFactory(
        destination_metadata={"drive": {"rejected_files_s3_key": "ws.zip"}}
    )

    with patch(
        "core.processing.rejected_files.ArchiveManager.get_presigned_url",
        return_value="http://s3.example.com/ws.zip?token=abc",
    ) as get_presigned_url:
        response = _owner_client(workspace).get(_rejected_files_url(workspace))

    assert response.status_code == 200
    assert response.json() == {"url": "http://s3.example.com/ws.zip?token=abc"}
    get_presigned_url.assert_called_once_with("ws.zip")


# ---------------------------------------------------------------------------
# integrity field
# ---------------------------------------------------------------------------


def _run(workspace, task_id, report=None, check_passed=None):
    return ExtraTaskInfo.objects.create(
        task_result=TaskResult.objects.create(task_id=task_id),
        workspace=workspace,
        integrity_report=report or {},
        integrity_check_passed=check_passed,
    )


def _report(migrated, source, sent=None):
    return {
        "summary": {
            "migrated_files_count": migrated,
            "source_files_count": source,
            "sent_files_count": sent,
        },
        "files": [],
    }


def _get_workspace(workspace):
    user = factories.UserFactory()
    user.workspaces.add(workspace)
    client = APIClient()
    client.force_login(user)
    response = client.get(f"/api/v1.0/workspaces/{workspace.id}/")
    assert response.status_code == 200
    return response.json()


def test_workspace_integrity_is_null_without_run():
    """A workspace never migrated has no integrity information."""
    workspace = factories.WorkspaceFactory()

    assert _get_workspace(workspace)["integrity"] is None


def test_workspace_integrity_comes_from_the_latest_run():
    """The file counts and check result of the latest run are exposed."""
    workspace = factories.WorkspaceFactory()
    _run(workspace, "t-1", _report(3, 3), check_passed=True)
    _run(workspace, "t-2", _report(5, 7, sent=6), check_passed=False)

    assert _get_workspace(workspace)["integrity"] == {
        "migrated_files_count": 5,
        "source_files_count": 7,
        "sent_files_count": 6,
        "check_passed": False,
    }


def test_workspace_integrity_sent_count_is_null_without_checked_destination():
    """Without destination checked file by file, the sent count is unknown."""
    workspace = factories.WorkspaceFactory()
    _run(workspace, "t-1", _report(3, 3), check_passed=True)

    assert _get_workspace(workspace)["integrity"]["sent_files_count"] is None


def test_workspace_integrity_is_null_when_latest_run_has_no_report():
    """An older report is not shown for a run migrated without the check."""
    workspace = factories.WorkspaceFactory()
    _run(workspace, "t-1", _report(3, 3), check_passed=True)
    _run(workspace, "t-2")

    assert _get_workspace(workspace)["integrity"] is None


def test_list_workspaces_reads_integrity_without_extra_query_per_workspace(
    django_assert_max_num_queries,
):
    """The integrity information does not add one query per listed workspace."""
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)
    for index in range(3):
        workspace = factories.WorkspaceFactory()
        user.workspaces.add(workspace)
        _run(workspace, f"t-{index}", _report(1, 2), check_passed=True)

    with django_assert_max_num_queries(3):
        response = client.get("/api/v1.0/workspaces/")

    assert response.status_code == 200


def test_list_workspaces_is_ordered_by_status_then_title():
    """Workspaces are listed by status, then alphabetically ignoring case."""
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)
    for title, status in [
        ("beta", "SUCCESS"),
        ("Alpha", "SUCCESS"),
        ("gamma", "FAILURE"),
        ("delta", "NONE"),
    ]:
        user.workspaces.add(factories.WorkspaceFactory(title=title, status=status))

    response = client.get("/api/v1.0/workspaces/")

    assert [w["title"] for w in response.json()["results"]] == [
        "gamma",
        "delta",
        "Alpha",
        "beta",
    ]
