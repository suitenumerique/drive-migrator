"""The export task emails that Resana folder access stays limited to the starter."""

from unittest.mock import patch

import pytest

from core.backends.source import SourceFolder
from core.factories import UserFactory, WorkspaceFactory
from core.processing.tasks import export

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def export_settings(settings, tmp_path):
    settings.APP_WORK_DIR = str(tmp_path)
    # Keep the task on its simplest path: no truncation, nothing under test here.
    settings.MIGRATION_FILE_LIMIT_PER_WORKSPACE = 0


def _run_export(workspace, user, *, begin_error=None):
    data = {"workspace": {"id": str(workspace.id)}, "user": {"id": str(user.id)}}
    with (
        patch("core.models.Workspace.objects.get", return_value=workspace),
        patch("core.models.User.objects.get", return_value=user),
        patch("core.processing.tasks.SourceManager") as mock_sm,
        patch("core.processing.tasks.FolderCreator") as mock_fc,
        patch("core.processing.tasks.DestinationRegistry") as mock_dr,
        patch("core.processing.tasks.MailsManager") as mock_mails,
    ):
        source_backend = mock_sm.return_value.get_backend.return_value
        source_backend.get_workspace_structure.return_value = SourceFolder(name="root")
        if begin_error is not None:
            source_backend.begin_export.side_effect = begin_error
        mock_fc.return_value.create_folder.return_value = "/tmp/ws-1"
        mock_fc.return_value.failed_files = []
        mock_dr.get_all.return_value = []
        if begin_error is None:
            export(data)  # pylint: disable=no-value-for-parameter
        else:
            with pytest.raises(type(begin_error), match=str(begin_error)):
                export(data)  # pylint: disable=no-value-for-parameter
    return mock_mails


def test_export_emails_removed_folders_to_the_starter():
    """The user who started the task receives the folder list, once."""
    user = UserFactory(email="starter@example.com")
    workspace = WorkspaceFactory(
        source_type="resana",
        source_lock_state={"permissions_removed": [{"id": "child", "name": "Secret"}]},
    )
    mock_mails = _run_export(workspace, user)

    mock_mails.return_value.send_migration_mail.assert_called_once_with(
        user,
        workspace,
        "folder_permissions_removed",
        {
            "title": (
                f"Les accès aux dossiers de l'espace {workspace.title} ont été limités"
            ),
            "access_limited": True,
            "removed_folders": ["Secret"],
            "duplicate_files": [],
        },
    )
    workspace.refresh_from_db()
    assert workspace.source_lock_state["permissions_mail_sent"] is True


def test_export_emails_when_only_folders_were_granted():
    """saveDroit left in place is enough to notify, even with no dropped droits."""
    user = UserFactory(email="starter@example.com")
    workspace = WorkspaceFactory(
        source_type="resana",
        source_lock_state={"folders_granted_by_us": ["root"]},
    )
    mock_mails = _run_export(workspace, user)

    mock_mails.return_value.send_migration_mail.assert_called_once_with(
        user,
        workspace,
        "folder_permissions_removed",
        {
            "title": (
                f"Les accès aux dossiers de l'espace {workspace.title} ont été limités"
            ),
            "access_limited": True,
            "removed_folders": [],
            "duplicate_files": [],
        },
    )
    workspace.refresh_from_db()
    assert workspace.source_lock_state["permissions_mail_sent"] is True


def test_export_does_not_email_when_no_folder_permission_was_removed():
    workspace = WorkspaceFactory(source_type="resana", source_lock_state={})
    mock_mails = _run_export(workspace, UserFactory())
    mock_mails.return_value.send_migration_mail.assert_not_called()


def test_export_does_not_email_removed_folders_twice():
    workspace = WorkspaceFactory(
        source_type="resana",
        source_lock_state={
            "permissions_removed": [{"id": "child", "name": "Secret"}],
            "permissions_mail_sent": True,
        },
    )
    mock_mails = _run_export(workspace, UserFactory())
    mock_mails.return_value.send_migration_mail.assert_not_called()


def test_export_emails_removed_folders_when_the_migration_fails():
    """The mail still goes out: the permissions are already deleted."""
    workspace = WorkspaceFactory(
        source_type="resana",
        source_lock_state={"permissions_removed": [{"id": "child", "name": "Secret"}]},
    )
    mock_mails = _run_export(
        workspace, UserFactory(), begin_error=RuntimeError("grant failed")
    )

    mock_mails.return_value.send_migration_mail.assert_called_once()
    workspace.refresh_from_db()
    assert workspace.source_lock_state["permissions_mail_sent"] is True


def test_export_emails_duplicate_files_without_a_permission_change():
    """A name or path clash is mailed once, without claiming access changed."""
    user = UserFactory(email="starter@example.com")
    duplicates = [{"name": "open.odt", "path": "Dossier"}]
    workspace = WorkspaceFactory(
        source_type="resana",
        source_lock_state={"duplicate_files": duplicates},
    )
    mock_mails = _run_export(workspace, user)

    mock_mails.return_value.send_migration_mail.assert_called_once_with(
        user,
        workspace,
        "folder_permissions_removed",
        {
            "title": f"Des fichiers en double dans l'espace {workspace.title}",
            "access_limited": False,
            "removed_folders": [],
            "duplicate_files": duplicates,
        },
    )
    workspace.refresh_from_db()
    assert workspace.source_lock_state["permissions_mail_sent"] is True
