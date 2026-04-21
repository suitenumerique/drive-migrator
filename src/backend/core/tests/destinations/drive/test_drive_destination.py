"""Tests for DriveDestinationBackend."""

from unittest.mock import MagicMock, patch

import pytest
import requests

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
def test_export_shares_with_migration_user_when_exchange_refused(mock_backend_cls, tmp_path):
    """export() falls back to sharing when the user exists in Keycloak but exchange is refused."""
    member = MagicMock()
    member.email = "alice@example.com"
    workspace = _make_workspace(migration_user=member)
    workspace.members = []
    user = MagicMock()

    mock_backend = mock_backend_cls.return_value
    mock_backend.get_access_token.return_value = "svc-tok"
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    mock_backend.find_user_sub_by_email.return_value = "user-sub"
    mock_backend.exchange_token.side_effect = requests.HTTPError("403")
    mock_backend.find_user_by_email.return_value = {"id": "user-uuid"}

    DriveDestinationBackend().export(workspace, user, str(tmp_path))

    mock_backend.create_folder.assert_called_once_with(workspace.title, token="svc-tok")
    mock_backend.find_user_by_email.assert_called_once_with("alice@example.com", token="svc-tok")
    mock_backend.share_with_user.assert_called_once_with("root-uuid", "user-uuid", token="svc-tok")


@patch("core.destinations.drive.backend.DriveBackend")
def test_export_invites_migration_user_when_not_in_keycloak(mock_backend_cls, tmp_path):
    """export() invites migration_user by email when they are absent from Keycloak Drive."""
    member = MagicMock()
    member.email = "new@example.com"
    workspace = _make_workspace(migration_user=member)
    workspace.members = []
    user = MagicMock()

    mock_backend = mock_backend_cls.return_value
    mock_backend.get_access_token.return_value = "svc-tok"
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    mock_backend.find_user_sub_by_email.return_value = None  # not in Keycloak → not in Drive
    mock_backend.find_user_by_email.return_value = None

    DriveDestinationBackend().export(workspace, user, str(tmp_path))

    mock_backend.invite_by_email.assert_called_once_with(
        "root-uuid", "new@example.com", token="svc-tok"
    )


# ---------------------------------------------------------------------------
# export() — token exchange (items in "Mes fichiers")
# ---------------------------------------------------------------------------


@patch("core.destinations.drive.backend.DriveBackend")
def test_export_uses_user_token_when_exchange_succeeds(mock_backend_cls, tmp_path):
    """export() creates items with the user token so they land in their 'Mes fichiers'."""
    member = MagicMock()
    member.email = "alice@example.com"
    workspace = _make_workspace(migration_user=member)
    workspace.members = []
    user = MagicMock()

    mock_backend = mock_backend_cls.return_value
    mock_backend.get_access_token.return_value = "svc-tok"
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    mock_backend.find_user_sub_by_email.return_value = "user-sub"
    mock_backend.exchange_token.return_value = "user-tok"

    DriveDestinationBackend().export(workspace, user, str(tmp_path))

    mock_backend.create_folder.assert_called_once_with(workspace.title, token="user-tok")
    mock_backend.find_user_by_email.assert_not_called()
    mock_backend.share_with_user.assert_not_called()
    mock_backend.invite_by_email.assert_not_called()



@patch("core.destinations.drive.backend.DriveBackend")
def test_export_raises_when_admin_api_fails(mock_backend_cls, tmp_path):
    """export() propagates an error from the Keycloak admin API instead of silently falling back."""
    member = MagicMock()
    member.email = "alice@example.com"
    workspace = _make_workspace(migration_user=member)
    user = MagicMock()

    mock_backend = mock_backend_cls.return_value
    mock_backend.get_access_token.return_value = "svc-tok"
    mock_backend.find_user_sub_by_email.side_effect = requests.HTTPError("503")

    with pytest.raises(requests.HTTPError):
        DriveDestinationBackend().export(workspace, user, str(tmp_path))


# ---------------------------------------------------------------------------
# _resolve_user_token()
# ---------------------------------------------------------------------------


def test_resolve_user_token_returns_user_token_when_exchange_succeeds():
    """_resolve_user_token() returns the user token on successful exchange."""
    mock_backend = MagicMock()
    mock_backend.find_user_sub_by_email.return_value = "user-sub"
    mock_backend.exchange_token.return_value = "user-tok"

    result = DriveDestinationBackend()._resolve_user_token(
        mock_backend, "svc-tok", "alice@example.com"
    )

    mock_backend.find_user_sub_by_email.assert_called_once_with("alice@example.com", "svc-tok")
    mock_backend.exchange_token.assert_called_once_with("svc-tok", "user-sub")
    assert result == "user-tok"


def test_resolve_user_token_returns_none_when_user_not_in_keycloak():
    """_resolve_user_token() returns None when the user has no Keycloak sub."""
    mock_backend = MagicMock()
    mock_backend.find_user_sub_by_email.return_value = None

    result = DriveDestinationBackend()._resolve_user_token(
        mock_backend, "svc-tok", "unknown@example.com"
    )

    mock_backend.exchange_token.assert_not_called()
    assert result is None


def test_resolve_user_token_returns_none_when_exchange_fails():
    """_resolve_user_token() returns None when the token exchange is refused (fallback)."""
    mock_backend = MagicMock()
    mock_backend.find_user_sub_by_email.return_value = "user-sub"
    mock_backend.exchange_token.side_effect = requests.HTTPError("403 Forbidden")

    result = DriveDestinationBackend()._resolve_user_token(
        mock_backend, "svc-tok", "alice@example.com"
    )

    assert result is None


def test_resolve_user_token_propagates_admin_api_error():
    """_resolve_user_token() lets admin API errors bubble up — config is broken, not a fallback."""
    mock_backend = MagicMock()
    mock_backend.find_user_sub_by_email.side_effect = requests.HTTPError("503")

    with pytest.raises(requests.HTTPError):
        DriveDestinationBackend()._resolve_user_token(
            mock_backend, "svc-tok", "alice@example.com"
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


# ---------------------------------------------------------------------------
# export() — workspace members sharing from workspace.members
# ---------------------------------------------------------------------------


@patch("core.destinations.drive.backend.DriveBackend")
def test_export_shares_with_known_workspace_members(mock_backend_cls, tmp_path):
    """export() shares the workspace with each member in workspace.members who exists in Drive."""
    workspace = _make_workspace()
    workspace.members = [
        {"name": "Dupont", "firstName": "Jean", "email": "jean@example.com"},
        {"name": "Martin", "firstName": "Alice", "email": "alice@example.com"},
    ]
    user = MagicMock()
    mock_backend = mock_backend_cls.return_value
    mock_backend.get_access_token.return_value = "tok"
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    mock_backend.find_user_by_email.return_value = {"id": "user-uuid"}

    DriveDestinationBackend().export(workspace, user, str(tmp_path))

    emails = [call.args[0] for call in mock_backend.find_user_by_email.call_args_list]
    assert "jean@example.com" in emails
    assert "alice@example.com" in emails
    assert mock_backend.share_with_user.call_count == 2


@patch("core.destinations.drive.backend.DriveBackend")
def test_export_invites_unknown_workspace_members(mock_backend_cls, tmp_path):
    """export() invites by email members from workspace.members not yet registered in Drive."""
    workspace = _make_workspace()
    workspace.members = [
        {"name": "Dupont", "firstName": "Jean", "email": "jean@example.com"},
    ]
    user = MagicMock()
    mock_backend = mock_backend_cls.return_value
    mock_backend.get_access_token.return_value = "tok"
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    mock_backend.find_user_by_email.return_value = None

    DriveDestinationBackend().export(workspace, user, str(tmp_path))

    mock_backend.invite_by_email.assert_called_with(
        "root-uuid", "jean@example.com", token="tok"
    )


@patch("core.destinations.drive.backend.DriveBackend")
def test_export_skips_member_sharing_when_members_empty(mock_backend_cls, tmp_path):
    """export() does not call find_user_by_email when workspace.members is empty."""
    workspace = _make_workspace(migration_user=None)
    workspace.members = []
    user = MagicMock()
    mock_backend = mock_backend_cls.return_value
    mock_backend.get_access_token.return_value = "tok"
    mock_backend.create_folder.return_value = {"id": "root-uuid"}

    DriveDestinationBackend().export(workspace, user, str(tmp_path))

    mock_backend.find_user_by_email.assert_not_called()
    mock_backend.share_with_user.assert_not_called()
    mock_backend.invite_by_email.assert_not_called()
