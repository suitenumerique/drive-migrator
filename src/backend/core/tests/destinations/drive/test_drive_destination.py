"""Tests for DriveDestinationBackend."""

from unittest.mock import MagicMock, patch

import pytest

from core.backends.destination import AbstractDestinationBackend
from core.destinations.drive.backend import DriveDestinationBackend
from core.models import Workspace


@pytest.fixture(autouse=True)
def _patch_mails_manager():
    """Prevent real MailsManager calls (DB access) across all tests in this module."""
    with patch("core.destinations.drive.backend.MailsManager") as mock_cls:
        mock_cls.return_value.send_migration_mail = MagicMock()
        yield mock_cls


def test_implements_abstract_destination():
    assert issubclass(DriveDestinationBackend, AbstractDestinationBackend)


def test_name_is_drive():
    assert DriveDestinationBackend.name == "drive"


def test_label_is_set():
    assert isinstance(DriveDestinationBackend.label, str)
    assert DriveDestinationBackend.label != ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workspace(migration_user=None, members=None):
    ws = MagicMock(spec=Workspace)
    ws.title = "My Workspace"
    ws.destination_statuses = {}
    ws.migration_user = migration_user
    ws.members = members or []
    return ws


def _make_migration_user(email="alice@example.com"):
    member = MagicMock()
    member.email = email
    return member


# ---------------------------------------------------------------------------
# service_account mode (DRIVE_AUTH_MODE = "service_account")
# ---------------------------------------------------------------------------


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_service_account_mode_uses_service_account_backend(
    mock_cls, tmp_path, settings
):
    """In service_account mode, export() instantiates DriveServiceAccountBackend."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_backend = mock_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}

    DriveDestinationBackend().export(_make_workspace(), MagicMock(), str(tmp_path))

    mock_cls.assert_called_once_with()


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_service_account_mode_creates_root_folder(mock_cls, tmp_path, settings):
    """export() creates a root Drive folder named after the workspace title."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_backend = mock_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    workspace = _make_workspace()

    DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    mock_backend.create_folder.assert_called_once_with("My Workspace")


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_service_account_mode_stores_root_id_in_metadata(mock_cls, tmp_path, settings):
    """export() stores the root folder ID in destination_metadata."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_backend = mock_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    workspace = _make_workspace()

    DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    workspace.set_destination_metadata.assert_called_once_with(
        "drive", {"workspace_id": "root-uuid"}
    )


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_service_account_mode_creates_subfolders(mock_cls, tmp_path, settings):
    """export() mirrors the local folder tree as Drive subfolders."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_backend = mock_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    mock_backend.create_subfolder.return_value = {"id": "sub-uuid"}
    (tmp_path / "docs").mkdir()

    DriveDestinationBackend().export(_make_workspace(), MagicMock(), str(tmp_path))

    mock_backend.create_subfolder.assert_called_once_with("docs", parent_id="root-uuid")


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_service_account_mode_uploads_files(mock_cls, tmp_path, settings):
    """export() uploads each file via the 3-step Drive upload process."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_backend = mock_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    mock_backend.create_file_item.return_value = {
        "id": "file-uuid",
        "policy": "https://s3.example.com/file.pdf?sig=x",
    }
    (tmp_path / "report.pdf").write_bytes(b"content")

    DriveDestinationBackend().export(_make_workspace(), MagicMock(), str(tmp_path))

    mock_backend.create_file_item.assert_called_once_with(
        "report.pdf", parent_id="root-uuid"
    )
    mock_backend.upload_to_s3.assert_called_once_with(
        "https://s3.example.com/file.pdf?sig=x", str(tmp_path / "report.pdf")
    )
    mock_backend.notify_upload_ended.assert_called_once_with("file-uuid")


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_service_account_mode_shares_with_migration_user_in_drive(
    mock_cls, tmp_path, settings
):
    """In service_account mode, migration_user is shared when they exist in Drive."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_backend = mock_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    mock_backend.find_user_by_email.return_value = {"id": "user-uuid"}
    workspace = _make_workspace(
        migration_user=_make_migration_user("alice@example.com")
    )

    DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    mock_backend.find_user_by_email.assert_any_call("alice@example.com")
    mock_backend.share_with_user.assert_any_call("root-uuid", "user-uuid")


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_service_account_mode_invites_migration_user_not_in_drive(
    mock_cls, tmp_path, settings
):
    """In service_account mode, migration_user is invited when not registered in Drive."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_backend = mock_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    mock_backend.find_user_by_email.return_value = None
    workspace = _make_workspace(migration_user=_make_migration_user("new@example.com"))

    DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    mock_backend.invite_by_email.assert_any_call("root-uuid", "new@example.com")


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_service_account_mode_skips_sharing_when_no_migration_user(
    mock_cls, tmp_path, settings
):
    """In service_account mode, sharing is skipped when migration_user is None."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_backend = mock_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}

    DriveDestinationBackend().export(
        _make_workspace(migration_user=None), MagicMock(), str(tmp_path)
    )

    mock_backend.find_user_by_email.assert_not_called()
    mock_backend.share_with_user.assert_not_called()
    mock_backend.invite_by_email.assert_not_called()


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_service_account_mode_shares_with_workspace_members_in_drive(
    mock_cls, tmp_path, settings
):
    """In service_account mode, workspace members who exist in Drive are shared."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_backend = mock_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    mock_backend.find_user_by_email.return_value = {"id": "user-uuid"}
    workspace = _make_workspace(
        members=[
            {"email": "jean@example.com"},
            {"email": "alice@example.com"},
        ]
    )

    DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    emails_queried = [c.args[0] for c in mock_backend.find_user_by_email.call_args_list]
    assert "jean@example.com" in emails_queried
    assert "alice@example.com" in emails_queried
    assert mock_backend.share_with_user.call_count == 2


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_service_account_mode_invites_unknown_workspace_members(
    mock_cls, tmp_path, settings
):
    """In service_account mode, members not in Drive are invited by email."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_backend = mock_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    mock_backend.find_user_by_email.return_value = None
    workspace = _make_workspace(members=[{"email": "jean@example.com"}])

    DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    mock_backend.invite_by_email.assert_called_with("root-uuid", "jean@example.com")


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_service_account_mode_sets_status_success(mock_cls, tmp_path, settings):
    """export() sets destination status to SUCCESS after successful upload."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_backend = mock_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    workspace = _make_workspace()

    DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    workspace.set_destination_status.assert_called_once_with(
        "drive", Workspace.Status.SUCCESS
    )
    workspace.save.assert_called()


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_service_account_mode_does_not_double_share_migration_user_who_is_also_member(
    mock_cls, tmp_path, settings
):
    """Migration user who is also a workspace member is shared only once."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_backend = mock_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    mock_backend.find_user_by_email.return_value = {"id": "user-uuid"}
    workspace = _make_workspace(
        migration_user=_make_migration_user("alice@example.com"),
        members=[{"email": "alice@example.com"}],
    )

    DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    emails_queried = [c.args[0] for c in mock_backend.find_user_by_email.call_args_list]
    assert emails_queried.count("alice@example.com") == 1


# ---------------------------------------------------------------------------
# user_token mode (DRIVE_AUTH_MODE = "user_token")
# ---------------------------------------------------------------------------


@patch("core.destinations.drive.backend.DriveUserTokenBackend")
def test_user_token_mode_uses_user_token_backend(mock_cls, tmp_path, settings):
    """In user_token mode, export() instantiates DriveUserTokenBackend with the user."""
    settings.DRIVE_AUTH_MODE = "user_token"
    mock_backend = mock_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    user = MagicMock()

    DriveDestinationBackend().export(_make_workspace(), user, str(tmp_path))

    mock_cls.assert_called_once_with(user)


@patch("core.destinations.drive.backend.DriveUserTokenBackend")
def test_user_token_mode_does_not_share_with_migration_user(
    mock_cls, tmp_path, settings
):
    """In user_token mode, migration_user is already owner — sharing is skipped."""
    settings.DRIVE_AUTH_MODE = "user_token"
    mock_backend = mock_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    workspace = _make_workspace(
        migration_user=_make_migration_user("alice@example.com")
    )

    DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    emails_queried = [c.args[0] for c in mock_backend.find_user_by_email.call_args_list]
    assert "alice@example.com" not in emails_queried


@patch("core.destinations.drive.backend.DriveUserTokenBackend")
def test_user_token_mode_still_shares_with_other_members(mock_cls, tmp_path, settings):
    """In user_token mode, other workspace members (not the migration user) are still shared."""
    settings.DRIVE_AUTH_MODE = "user_token"
    mock_backend = mock_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    mock_backend.find_user_by_email.return_value = {"id": "other-user-uuid"}
    workspace = _make_workspace(
        migration_user=_make_migration_user("alice@example.com"),
        members=[
            {"email": "alice@example.com"},  # should be skipped
            {"email": "bob@example.com"},  # should be shared
        ],
    )

    DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    emails_queried = [c.args[0] for c in mock_backend.find_user_by_email.call_args_list]
    assert "alice@example.com" not in emails_queried
    assert "bob@example.com" in emails_queried


@patch("core.destinations.drive.backend.DriveUserTokenBackend")
def test_user_token_mode_shares_with_members_even_without_migration_user(
    mock_cls, tmp_path, settings
):
    """In user_token mode with no migration_user, members are still shared normally."""
    settings.DRIVE_AUTH_MODE = "user_token"
    mock_backend = mock_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    mock_backend.find_user_by_email.return_value = {"id": "user-uuid"}
    workspace = _make_workspace(
        migration_user=None,
        members=[{"email": "bob@example.com"}],
    )

    DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    emails_queried = [c.args[0] for c in mock_backend.find_user_by_email.call_args_list]
    assert "bob@example.com" in emails_queried


@patch("core.destinations.drive.backend.DriveUserTokenBackend")
def test_user_token_mode_sets_status_success(mock_cls, tmp_path, settings):
    """In user_token mode, export() sets destination status to SUCCESS."""
    settings.DRIVE_AUTH_MODE = "user_token"
    mock_backend = mock_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    workspace = _make_workspace()

    DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    workspace.set_destination_status.assert_called_once_with(
        "drive", Workspace.Status.SUCCESS
    )


# ---------------------------------------------------------------------------
# Completion mail
# ---------------------------------------------------------------------------


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_export_sends_drive_ready_mail(
    mock_backend_cls, tmp_path, settings, _patch_mails_manager
):
    """export() sends a 'drive_ready' migration mail to the user after upload."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_backend = mock_backend_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    workspace = _make_workspace()
    user = MagicMock()

    DriveDestinationBackend().export(workspace, user, str(tmp_path))

    mock_send = _patch_mails_manager.return_value.send_migration_mail
    mock_send.assert_called_once()
    args, kwargs = mock_send.call_args
    assert args[:3] == (user, workspace, "drive_ready")
    assert str(args[3]["title"])
    assert kwargs == {}
