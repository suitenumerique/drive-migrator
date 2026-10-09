"""Tests for DriveDestinationBackend."""

from unittest.mock import ANY, MagicMock, patch

import pytest
from requests.exceptions import HTTPError

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
    """DriveDestinationBackend satisfies the AbstractDestinationBackend interface."""
    assert issubclass(DriveDestinationBackend, AbstractDestinationBackend)


def test_name_is_drive():
    """name class attribute is 'drive'."""
    assert DriveDestinationBackend.name == "drive"


def test_label_is_set():
    """label class attribute is a non-empty string."""
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
        "report.pdf", parent_id="root-uuid", size=7, item_id=ANY
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


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_sharing_disabled_skips_share_members(mock_cls, tmp_path, settings):
    """When DRIVE_SHARE_MEMBERS is False, no sharing or invitation calls are made."""
    settings.DRIVE_AUTH_MODE = "service_account"
    settings.DRIVE_SHARE_MEMBERS = False
    mock_backend = mock_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    workspace = _make_workspace(
        migration_user=_make_migration_user("alice@example.com"),
        members=[{"email": "bob@example.com"}],
    )

    DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    mock_backend.find_user_by_email.assert_not_called()
    mock_backend.share_with_user.assert_not_called()
    mock_backend.invite_by_email.assert_not_called()


# ---------------------------------------------------------------------------
# users_list_by_migrator.csv (shared users list, mirrors the archive export)
# ---------------------------------------------------------------------------


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_uploads_users_csv_when_workspace_has_members(mock_cls, tmp_path, settings):
    """export() uploads a users_list_by_migrator.csv listing the shared members, like the zip export."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_backend = mock_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    mock_backend.create_file_item.return_value = {
        "id": "file-uuid",
        "policy": "https://s3.example.com/users_list_by_migrator.csv?sig=x",
    }
    workspace = _make_workspace(
        members=[{"name": "Doe", "firstName": "Jean", "email": "jean@example.com"}]
    )

    DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    mock_backend.create_file_item.assert_any_call(
        "users_list_by_migrator.csv", parent_id="root-uuid", size=ANY, item_id=ANY
    )
    mock_backend.upload_to_s3.assert_any_call(
        "https://s3.example.com/users_list_by_migrator.csv?sig=x",
        str(tmp_path / "users_list_by_migrator.csv"),
    )


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_users_csv_removed_from_local_folder_after_upload(mock_cls, tmp_path, settings):
    """The temporary users_list_by_migrator.csv is cleaned up from the local folder after export."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_backend = mock_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    mock_backend.create_file_item.return_value = {
        "id": "file-uuid",
        "policy": "https://s3.example.com/users_list_by_migrator.csv?sig=x",
    }
    workspace = _make_workspace(
        members=[{"name": "Doe", "firstName": "Jean", "email": "jean@example.com"}]
    )

    DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    assert not (tmp_path / "users_list_by_migrator.csv").exists()


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_no_users_csv_uploaded_when_workspace_has_no_members(
    mock_cls, tmp_path, settings
):
    """No users_list_by_migrator.csv is created or uploaded when the workspace has no members."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_backend = mock_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    workspace = _make_workspace(members=[])

    DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    uploaded_names = [c.args[0] for c in mock_backend.create_file_item.call_args_list]
    assert "users_list_by_migrator.csv" not in uploaded_names


# ---------------------------------------------------------------------------
# upload_errors (files rejected by Drive)
# ---------------------------------------------------------------------------


def _reject_one_file(destination, *args):  # pylint: disable=unused-argument
    """Stand-in for _upload_tree recording a file refused by Drive."""
    destination._upload_log.rejected.append(  # pylint: disable=protected-access
        {"path": "page.txt", "item_id": "file-uuid", "error": "file_type_not_allowed"}
    )


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_export_persists_rejected_files_on_workspace(mock_cls, tmp_path, settings):
    """export() saves the files Drive refused onto the workspace's upload_errors."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_cls.return_value.create_folder.return_value = {"id": "root-uuid"}
    workspace = _make_workspace()

    with patch.object(
        DriveDestinationBackend, "_upload_tree", autospec=True
    ) as upload_tree:
        upload_tree.side_effect = _reject_one_file
        DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    assert workspace.upload_errors == [
        {"path": "page.txt", "item_id": "file-uuid", "error": "file_type_not_allowed"}
    ]
    workspace.save.assert_any_call(update_fields=["upload_errors"])


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_export_resets_upload_errors_when_nothing_is_rejected(
    mock_cls, tmp_path, settings
):
    """A run without rejection clears the upload_errors of a previous run."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_cls.return_value.create_folder.return_value = {"id": "root-uuid"}
    workspace = _make_workspace()
    workspace.upload_errors = [{"path": "old.txt"}]

    DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    assert not workspace.upload_errors


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_export_persists_rejected_files_when_upload_fails(mock_cls, tmp_path, settings):
    """Files refused before a fatal upload error are still saved."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_cls.return_value.create_folder.return_value = {"id": "root-uuid"}
    workspace = _make_workspace()

    def reject_then_fail(destination, *args):
        _reject_one_file(destination)
        raise RuntimeError("boom")

    with patch.object(
        DriveDestinationBackend, "_upload_tree", autospec=True
    ) as upload_tree:
        upload_tree.side_effect = reject_then_fail
        with pytest.raises(RuntimeError):
            DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    assert len(workspace.upload_errors) == 1
    workspace.save.assert_called_once_with(update_fields=["upload_errors"])


def _upload(sent, rejected):
    """Stand-in for _upload_tree sending the given paths, Drive refusing some."""

    def upload_tree(destination, *args):  # pylint: disable=unused-argument
        log = destination._upload_log  # pylint: disable=protected-access
        for path in sent:
            log.items[path] = f"id-{path}"
        for path in rejected:
            log.rejected.append(
                {
                    "path": path,
                    "item_id": f"id-{path}",
                    "error": "file_type_not_allowed",
                }
            )

    return upload_tree


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_export_fails_when_drive_rejects_every_file(mock_cls, tmp_path, settings):
    """A migration where Drive refused every file fails instead of reporting success."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_backend = mock_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    workspace = _make_workspace(migration_user=_make_migration_user())

    with patch.object(
        DriveDestinationBackend, "_upload_tree", autospec=True
    ) as upload_tree:
        upload_tree.side_effect = _upload(["a.txt", "b.txt"], ["a.txt", "b.txt"])
        with pytest.raises(RuntimeError, match="All 2 file"):
            DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    assert len(workspace.upload_errors) == 2
    mock_backend.find_user_by_email.assert_not_called()
    workspace.set_destination_status.assert_not_called()


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_export_succeeds_when_drive_rejects_some_files(mock_cls, tmp_path, settings):
    """One file sent is enough for the migration to succeed with its gaps listed."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_cls.return_value.create_folder.return_value = {"id": "root-uuid"}
    workspace = _make_workspace()

    with patch.object(
        DriveDestinationBackend, "_upload_tree", autospec=True
    ) as upload_tree:
        upload_tree.side_effect = _upload(["a.txt", "b.txt"], ["a.txt"])
        DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    workspace.set_destination_status.assert_called_once_with(
        "drive", Workspace.Status.SUCCESS
    )


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_export_ignores_the_generated_users_csv_when_checking_rejections(
    mock_cls, tmp_path, settings
):
    """The members CSV is not a source file: sending it does not count, nor does
    its rejection in a workspace without files."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_cls.return_value.create_folder.return_value = {"id": "root-uuid"}
    workspace = _make_workspace()
    csv_name = "users_list_by_migrator.csv"

    with patch.object(
        DriveDestinationBackend, "_upload_tree", autospec=True
    ) as upload_tree:
        upload_tree.side_effect = _upload([csv_name, "a.txt"], ["a.txt"])
        with pytest.raises(RuntimeError):
            DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

        upload_tree.side_effect = _upload([csv_name], [csv_name])
        DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    workspace.set_destination_status.assert_called_once_with(
        "drive", Workspace.Status.SUCCESS
    )


def _drive_refusal(code):
    """Build the HTTPError Drive raises when it refuses a file."""
    response = MagicMock(status_code=400)
    response.json.return_value = {
        "type": "validation_error",
        "errors": [{"code": code, "detail": "Refused.", "attr": None}],
    }
    return HTTPError("400 Client Error", response=response)


def _mock_two_file_upload(mock_cls, tmp_path):
    """Two local files, each getting a Drive item named after it."""
    mock_backend = mock_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    mock_backend.create_file_item.side_effect = lambda name, parent_id, size, item_id: {
        "id": f"id-{name}",
        "policy": f"https://s3.example.com/{name}",
    }
    (tmp_path / "a.txt").write_bytes(b"a")
    (tmp_path / "b.txt").write_bytes(b"b")
    return mock_backend


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_file_refused_at_upload_ended_is_skipped(mock_cls, tmp_path, settings):
    """Drive refusing a file's content skips it: the next files are still sent."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_backend = _mock_two_file_upload(mock_cls, tmp_path)
    mock_backend.notify_upload_ended.side_effect = [
        _drive_refusal("file_type_not_allowed"),
        None,
    ]
    workspace = _make_workspace()

    DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    assert mock_backend.notify_upload_ended.call_count == 2
    assert workspace.upload_errors == [
        {"path": "a.txt", "item_id": "id-a.txt", "error": "file_type_not_allowed"}
    ]
    workspace.set_destination_status.assert_called_once_with(
        "drive", Workspace.Status.SUCCESS
    )


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_file_refused_at_creation_is_skipped(mock_cls, tmp_path, settings):
    """A file refused at creation is listed with the id the migrator sent."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_backend = _mock_two_file_upload(mock_cls, tmp_path)
    create = mock_backend.create_file_item.side_effect
    mock_backend.create_file_item.side_effect = [
        _drive_refusal("item_create_file_extension_not_allowed"),
        create("b.txt", parent_id="root-uuid", size=1, item_id=None),
    ]
    workspace = _make_workspace()

    DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    sent_id = mock_backend.create_file_item.call_args_list[0].kwargs["item_id"]
    assert workspace.upload_errors == [
        {
            "path": "a.txt",
            "item_id": sent_id,
            "error": "item_create_file_extension_not_allowed",
        }
    ]
    mock_backend.upload_to_s3.assert_called_once_with(
        "https://s3.example.com/b.txt", str(tmp_path / "b.txt")
    )


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_other_drive_error_still_stops_the_upload(mock_cls, tmp_path, settings):
    """An error that is not a refusal of the file still stops the migration."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_backend = _mock_two_file_upload(mock_cls, tmp_path)
    mock_backend.notify_upload_ended.side_effect = _drive_refusal(
        "item_upload_type_unavailable"
    )
    workspace = _make_workspace()

    with pytest.raises(HTTPError):
        DriveDestinationBackend().export(workspace, MagicMock(), str(tmp_path))

    assert mock_backend.notify_upload_ended.call_count == 1
    assert not workspace.upload_errors


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_export_logs_rejected_files_once_without_paths(mock_cls, tmp_path, settings):
    """Rejections are summed up in a single ERROR, with item ids and codes only."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_cls.return_value.create_folder.return_value = {"id": "root-uuid"}

    with (
        patch.object(
            DriveDestinationBackend, "_upload_tree", autospec=True
        ) as upload_tree,
        patch("core.destinations.drive.backend.logger") as mock_logger,
    ):
        upload_tree.side_effect = _upload(["a.txt", "b.txt"], ["a.txt"])
        DriveDestinationBackend().export(_make_workspace(), MagicMock(), str(tmp_path))

    mock_logger.error.assert_called_once_with(
        "%s file(s) rejected by Drive: %s",
        1,
        [("id-a.txt", "file_type_not_allowed")],
    )


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_export_logs_nothing_at_error_without_rejection(mock_cls, tmp_path, settings):
    """A run where Drive accepted every file produces no Sentry event."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_cls.return_value.create_folder.return_value = {"id": "root-uuid"}
    (tmp_path / "report.pdf").write_bytes(b"content")

    with patch("core.destinations.drive.backend.logger") as mock_logger:
        DriveDestinationBackend().export(_make_workspace(), MagicMock(), str(tmp_path))

    mock_logger.error.assert_not_called()


@patch("core.destinations.drive.backend.DriveServiceAccountBackend")
def test_export_logs_each_file_before_creating_it(mock_cls, tmp_path, settings):
    """Each upload is logged with its item id and size, before Drive is called."""
    settings.DRIVE_AUTH_MODE = "service_account"
    mock_backend = mock_cls.return_value
    mock_backend.create_folder.return_value = {"id": "root-uuid"}
    mock_backend.create_file_item.side_effect = RuntimeError("boom")
    (tmp_path / "report.pdf").write_bytes(b"content")

    with (
        patch("core.destinations.drive.backend.logger") as mock_logger,
        pytest.raises(RuntimeError),
    ):
        DriveDestinationBackend().export(_make_workspace(), MagicMock(), str(tmp_path))

    item_id = mock_backend.create_file_item.call_args.kwargs["item_id"]
    mock_logger.info.assert_called_once_with("Uploading file %s (%s bytes)", item_id, 7)


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
