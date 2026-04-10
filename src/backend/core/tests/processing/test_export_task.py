"""Tests for the generic export Celery task."""

from unittest.mock import MagicMock, patch

import pytest

from core.backends.source import SourceFolder
from core.models import User, Workspace


@pytest.fixture()
def workspace():
    ws = MagicMock(spec=Workspace)
    ws.id = "ws-1"
    ws.title = "My Workspace"
    ws.destination_statuses = {}
    ws.get_destination_status.return_value = Workspace.Status.PENDING
    return ws


@pytest.fixture()
def user():
    u = MagicMock(spec=User)
    u.id = "user-1"
    return u


def _run_export(workspace, user, dest_backends=None, source_folder=None):
    """Helper: run the export task with standard mocks in place."""
    if dest_backends is None:
        dest_backends = []
    if source_folder is None:
        source_folder = SourceFolder(name="root")

    with (
        patch("core.models.Workspace.objects.get", return_value=workspace),
        patch("core.models.User.objects.get", return_value=user),
        patch("core.processing.tasks.SourceManager") as mock_source_manager_cls,
        patch("core.processing.tasks.FolderCreator") as mock_folder_creator_cls,
        patch("core.processing.tasks.DestinationRegistry") as mock_dest_registry,
    ):
        mock_source_backend = (
            mock_source_manager_cls.return_value.get_backend.return_value
        )
        mock_source_backend.get_workspace_structure.return_value = source_folder
        mock_folder_creator_cls.return_value.create_folder.return_value = "/tmp/ws-1"
        mock_dest_registry.get_all.return_value = dest_backends

        from core.processing.tasks import export

        export({"workspace": {"id": "ws-1"}, "user": {"id": "user-1"}})

        return (
            mock_source_backend,
            mock_folder_creator_cls.return_value,
            mock_dest_registry,
        )


def test_export_fetches_workspace_and_user(workspace, user):
    """export() loads Workspace and User from the database."""
    with (
        patch(
            "core.models.Workspace.objects.get", return_value=workspace
        ) as mock_ws_get,
        patch("core.models.User.objects.get", return_value=user) as mock_user_get,
        patch("core.processing.tasks.SourceManager") as mock_sm,
        patch("core.processing.tasks.FolderCreator") as mock_fc,
        patch("core.processing.tasks.DestinationRegistry") as mock_dr,
    ):
        mock_sm.return_value.get_backend.return_value.get_workspace_structure.return_value = SourceFolder(
            name="root"
        )
        mock_fc.return_value.create_folder.return_value = "/tmp/ws-1"
        mock_dr.get_all.return_value = []

        from core.processing.tasks import export

        export({"workspace": {"id": "ws-1"}, "user": {"id": "user-1"}})

    mock_ws_get.assert_called_once_with(id="ws-1")
    mock_user_get.assert_called_once_with(id="user-1")


def test_export_calls_get_workspace_structure(workspace, user):
    """export() calls source_backend.get_workspace_structure() to get the folder tree."""
    source_backend, _, _ = _run_export(workspace, user)
    source_backend.get_workspace_structure.assert_called_once_with(workspace)


def test_export_builds_local_folder(workspace, user):
    """export() calls FolderCreator.create_folder() with workspace, folder tree, and source backend."""
    folder_tree = SourceFolder(name="root")
    source_backend, creator, _ = _run_export(workspace, user, source_folder=folder_tree)
    creator.create_folder.assert_called_once_with(
        workspace, folder_tree, source_backend
    )


def test_export_calls_prepare_export(workspace, user):
    """export() calls source_backend.prepare_export() with the local folder path."""
    source_backend, _, _ = _run_export(workspace, user)
    source_backend.prepare_export.assert_called_once_with(workspace, "/tmp/ws-1")


def test_export_calls_each_pending_destination(workspace, user):
    """export() calls export() on each destination backend whose status is PENDING."""
    workspace.get_destination_status.return_value = Workspace.Status.PENDING

    dest_archive = MagicMock()
    dest_archive.name = "archive"
    dest_resana = MagicMock()
    dest_resana.name = "resana"

    _run_export(workspace, user, dest_backends=[dest_archive, dest_resana])

    dest_archive.export.assert_called_once_with(workspace, user, "/tmp/ws-1")
    dest_resana.export.assert_called_once_with(workspace, user, "/tmp/ws-1")


def test_export_skips_non_pending_destinations(workspace, user):
    """export() skips destinations whose status is not PENDING."""
    dest_archive = MagicMock()
    dest_archive.name = "archive"
    dest_resana = MagicMock()
    dest_resana.name = "resana"

    def get_status(name):
        if name == "archive":
            return Workspace.Status.SUCCESS
        return Workspace.Status.PENDING

    workspace.get_destination_status.side_effect = get_status

    _run_export(workspace, user, dest_backends=[dest_archive, dest_resana])

    dest_archive.export.assert_not_called()
    dest_resana.export.assert_called_once()
