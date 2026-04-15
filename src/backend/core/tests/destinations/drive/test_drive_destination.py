"""Tests for DriveDestinationBackend."""

from unittest.mock import MagicMock, patch

from core.backends.destination import AbstractDestinationBackend
from core.destinations.drive.backend import DriveDestinationBackend
from core.models import Workspace


def test_implements_abstract_destination():
    assert issubclass(DriveDestinationBackend, AbstractDestinationBackend)


def test_name_is_drive():
    assert DriveDestinationBackend.name == "drive"


def test_label_is_set():
    assert isinstance(DriveDestinationBackend.label, str)
    assert DriveDestinationBackend.label != ""


# ---------------------------------------------------------------------------
# export()
# ---------------------------------------------------------------------------


def _make_workspace(migration_user=None, destination_statuses=None):
    ws = MagicMock(spec=Workspace)
    ws.title = "My Workspace"
    ws.destination_statuses = destination_statuses or {}
    ws.migration_user = migration_user
    return ws


@patch("core.destinations.drive.backend.DriveBackend")
def test_export_creates_root_folder(mock_backend_cls, tmp_path):
    """export() creates a root folder in Drive named after the workspace title."""
    workspace = _make_workspace()
    user = MagicMock()
    mock_backend = mock_backend_cls.return_value
    mock_backend.get_access_token.return_value = "tok"
    mock_backend.create_folder.return_value = {"id": "root-uuid"}

    backend = DriveDestinationBackend()
    backend.export(workspace, user, str(tmp_path))

    mock_backend.create_folder.assert_called_once_with(workspace.title, token="tok")


@patch("core.destinations.drive.backend.DriveBackend")
def test_export_stores_root_folder_id_in_metadata(mock_backend_cls, tmp_path):
    """export() stores the Drive root folder UUID in destination_metadata."""
    workspace = _make_workspace()
    user = MagicMock()
    mock_backend = mock_backend_cls.return_value
    mock_backend.get_access_token.return_value = "tok"
    mock_backend.create_folder.return_value = {"id": "root-uuid"}

    backend = DriveDestinationBackend()
    backend.export(workspace, user, str(tmp_path))

    workspace.set_destination_metadata.assert_called_once_with(
        "drive", {"workspace_id": "root-uuid"}
    )


@patch("core.destinations.drive.backend.DriveBackend")
def test_export_creates_subfolders(mock_backend_cls, tmp_path):
    """export() creates Drive subfolders mirroring the local directory structure."""
    workspace = _make_workspace()
    user = MagicMock()
    mock_backend = mock_backend_cls.return_value
    mock_backend.get_access_token.return_value = "tok"
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    mock_backend.create_subfolder.return_value = {"id": "sub-uuid"}

    # Create local folder structure
    (tmp_path / "docs").mkdir()

    backend = DriveDestinationBackend()
    backend.export(workspace, user, str(tmp_path))

    mock_backend.create_subfolder.assert_called_once_with(
        "docs", parent_id="root-uuid", token="tok"
    )


@patch("core.destinations.drive.backend.DriveBackend")
def test_export_uploads_files(mock_backend_cls, tmp_path):
    """export() uploads each file via the 3-step Drive upload process."""
    workspace = _make_workspace()
    user = MagicMock()
    mock_backend = mock_backend_cls.return_value
    mock_backend.get_access_token.return_value = "tok"
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    mock_backend.create_file_item.return_value = {
        "id": "file-uuid",
        "policy": "https://s3.example.com/file.pdf?sig=x",
    }

    (tmp_path / "report.pdf").write_bytes(b"content")

    backend = DriveDestinationBackend()
    backend.export(workspace, user, str(tmp_path))

    mock_backend.create_file_item.assert_called_once_with(
        "report.pdf", parent_id="root-uuid", token="tok"
    )
    mock_backend.upload_to_s3.assert_called_once_with(
        "https://s3.example.com/file.pdf?sig=x", str(tmp_path / "report.pdf")
    )
    mock_backend.notify_upload_ended.assert_called_once_with("file-uuid", token="tok")


@patch("core.destinations.drive.backend.DriveBackend")
def test_export_shares_with_migration_user_when_in_drive(mock_backend_cls, tmp_path):
    """export() shares the workspace with migration_user when they exist in Drive."""
    member = MagicMock()
    member.email = "alice@example.com"
    workspace = _make_workspace(migration_user=member)
    user = MagicMock()

    mock_backend = mock_backend_cls.return_value
    mock_backend.get_access_token.return_value = "tok"
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    mock_backend.find_user_by_email.return_value = {"id": "user-uuid"}

    backend = DriveDestinationBackend()
    backend.export(workspace, user, str(tmp_path))

    mock_backend.find_user_by_email.assert_called_once_with(
        "alice@example.com", token="tok"
    )
    mock_backend.share_with_user.assert_called_once_with(
        "root-uuid", "user-uuid", token="tok"
    )


@patch("core.destinations.drive.backend.DriveBackend")
def test_export_invites_migration_user_when_not_in_drive(mock_backend_cls, tmp_path):
    """export() invites migration_user by email when they are not yet registered in Drive."""
    member = MagicMock()
    member.email = "new@example.com"
    workspace = _make_workspace(migration_user=member)
    user = MagicMock()

    mock_backend = mock_backend_cls.return_value
    mock_backend.get_access_token.return_value = "tok"
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    mock_backend.find_user_by_email.return_value = None

    backend = DriveDestinationBackend()
    backend.export(workspace, user, str(tmp_path))

    mock_backend.invite_by_email.assert_called_once_with(
        "root-uuid", "new@example.com", token="tok"
    )


@patch("core.destinations.drive.backend.DriveBackend")
def test_export_skips_sharing_when_no_migration_user(mock_backend_cls, tmp_path):
    """export() skips Drive sharing when migration_user is None."""
    workspace = _make_workspace(migration_user=None)
    user = MagicMock()

    mock_backend = mock_backend_cls.return_value
    mock_backend.get_access_token.return_value = "tok"
    mock_backend.create_folder.return_value = {"id": "root-uuid"}

    backend = DriveDestinationBackend()
    backend.export(workspace, user, str(tmp_path))

    mock_backend.find_user_by_email.assert_not_called()
    mock_backend.share_with_user.assert_not_called()
    mock_backend.invite_by_email.assert_not_called()


@patch("core.destinations.drive.backend.DriveBackend")
def test_export_sets_status_success(mock_backend_cls, tmp_path):
    """export() sets destination status to SUCCESS after successful upload."""
    workspace = _make_workspace()
    user = MagicMock()
    mock_backend = mock_backend_cls.return_value
    mock_backend.get_access_token.return_value = "tok"
    mock_backend.create_folder.return_value = {"id": "root-uuid"}

    backend = DriveDestinationBackend()
    backend.export(workspace, user, str(tmp_path))

    workspace.set_destination_status.assert_called_once_with(
        "drive", Workspace.Status.SUCCESS
    )
    workspace.save.assert_called()
