"""Tests for ArchiveDestinationBackend."""

# pylint: disable=no-member  # django.core.mail.outbox is added by the locmem backend

from unittest.mock import patch

from django.core import mail

import pytest

from core import factories
from core.backends.destination import AbstractDestinationBackend
from core.destinations.archive.backend import ArchiveDestinationBackend
from core.models import Workspace

pytestmark = pytest.mark.django_db


def _workspace(**fields):
    fields.setdefault("members", [])
    fields.setdefault("title", "My Workspace")
    return factories.WorkspaceFactory(**fields)


def _user():
    return factories.UserFactory(email="user@example.com")


def test_export_sends_download_mail(settings):
    """export() sends the archive download email to the user after uploading."""
    settings.LOGIN_REDIRECT_URL = "http://localhost:3010"
    workspace = _workspace()
    user = _user()

    with patch("core.destinations.archive.backend.ArchiveManager") as mock_manager_cls:
        mock_manager = mock_manager_cls.return_value
        mock_manager.upload_archive.return_value = "http://s3.example.com/ws.zip"

        backend = ArchiveDestinationBackend()
        backend.export(workspace, user, "/tmp/workspace")

    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == [user.email]
    assert workspace.title in sent.subject
    assert "Votre archive est prête" in sent.body
    assert (
        f"http://localhost:3010/download-archive/?workspaceId={workspace.id}"
        in sent.body
    )


def test_export_mail_links_to_frontend_not_raw_s3_url(settings):
    """The mail must never carry the raw S3 presigned URL: it must link to the
    frontend download page, which will fetch a short-lived URL once authenticated."""
    settings.LOGIN_REDIRECT_URL = "http://localhost:3010"
    workspace = _workspace()
    user = _user()

    with patch("core.destinations.archive.backend.ArchiveManager") as mock_manager_cls:
        mock_manager = mock_manager_cls.return_value
        mock_manager.upload_archive.return_value = (
            "http://s3.example.com/ws.zip?sig=secret"
        )

        backend = ArchiveDestinationBackend()
        backend.export(workspace, user, "/tmp/workspace")

    body = mail.outbox[0].body
    assert f"http://localhost:3010/download-archive/?workspaceId={workspace.id}" in body
    assert "sig=secret" not in body


def test_implements_abstract_destination():
    """ArchiveDestinationBackend must be a concrete implementation of AbstractDestinationBackend."""
    assert issubclass(ArchiveDestinationBackend, AbstractDestinationBackend)


def test_name_is_archive():
    """name must be 'archive'."""
    assert ArchiveDestinationBackend.name == "archive"


def test_label_is_set():
    """label must be a non-empty string."""
    assert isinstance(ArchiveDestinationBackend.label, str)
    assert ArchiveDestinationBackend.label != ""


def test_export_creates_zip():
    """export() zips the local folder."""
    workspace = _workspace()
    user = _user()

    with patch("core.destinations.archive.backend.ArchiveManager") as mock_manager_cls:
        mock_manager = mock_manager_cls.return_value
        mock_manager.upload_archive.return_value = "http://s3.example.com/ws.zip"

        backend = ArchiveDestinationBackend()
        backend.export(workspace, user, "/tmp/workspace")

    mock_manager.zip_workspace_folder.assert_called_once_with(workspace)


def test_export_uploads_to_s3():
    """export() uploads the archive to S3."""
    workspace = _workspace()
    user = _user()

    with patch("core.destinations.archive.backend.ArchiveManager") as mock_manager_cls:
        mock_manager = mock_manager_cls.return_value
        mock_manager.upload_archive.return_value = "http://s3.example.com/ws.zip"

        backend = ArchiveDestinationBackend()
        backend.export(workspace, user, "/tmp/workspace")

    mock_manager.upload_archive.assert_called_once_with(workspace)


def test_export_sets_status_success():
    """export() sets destination status to SUCCESS on success."""
    workspace = _workspace()
    user = _user()

    with patch("core.destinations.archive.backend.ArchiveManager") as mock_manager_cls:
        mock_manager = mock_manager_cls.return_value
        mock_manager.upload_archive.return_value = "http://s3.example.com/ws.zip"

        backend = ArchiveDestinationBackend()
        backend.export(workspace, user, "/tmp/workspace")

    workspace.refresh_from_db()
    assert workspace.get_destination_status("archive") == Workspace.Status.SUCCESS


def test_export_includes_any_extra_files_in_local_folder():
    """export() zips the entire local folder, including any extra files (e.g. members CSV)."""
    workspace = _workspace()
    user = _user()

    with patch("core.destinations.archive.backend.ArchiveManager") as mock_manager_cls:
        mock_manager = mock_manager_cls.return_value
        mock_manager.upload_archive.return_value = "http://s3.example.com/ws.zip"

        backend = ArchiveDestinationBackend()
        backend.export(workspace, user, "/tmp/workspace")

    # zip_workspace_folder is called with the workspace (folder path is derived internally)
    # — the entire folder is zipped, whatever it contains.
    mock_manager.zip_workspace_folder.assert_called_once_with(workspace)


def test_export_writes_users_csv_then_removes_it(tmp_path):
    """export() writes users_list_by_migrator.csv before zipping then removes it so Drive won't upload it."""
    workspace = _workspace(
        members=[
            {"name": "Dupont", "firstName": "Jean", "email": "jean@example.com"},
        ]
    )
    user = _user()
    csv_path = tmp_path / "users_list_by_migrator.csv"

    def assert_csv_exists_during_zip(_ws):
        assert csv_path.exists()
        assert csv_path.read_text().strip() == "Dupont,Jean,jean@example.com"

    with patch("core.destinations.archive.backend.ArchiveManager") as mock_manager_cls:
        mock_manager = mock_manager_cls.return_value
        mock_manager.zip_workspace_folder.side_effect = assert_csv_exists_during_zip
        mock_manager.upload_archive.return_value = "http://s3.example.com/ws.zip"

        ArchiveDestinationBackend().export(workspace, user, str(tmp_path))

    assert not csv_path.exists()


def test_export_skips_users_csv_when_no_members(tmp_path):
    """export() does not write users_list_by_migrator.csv when workspace.members is empty."""
    workspace = _workspace()
    user = _user()

    with patch("core.destinations.archive.backend.ArchiveManager") as mock_manager_cls:
        mock_manager = mock_manager_cls.return_value
        mock_manager.upload_archive.return_value = "http://s3.example.com/ws.zip"

        backend = ArchiveDestinationBackend()
        backend.export(workspace, user, str(tmp_path))

    assert not (tmp_path / "users_list_by_migrator.csv").exists()


def test_get_download_url_returns_presigned_url():
    """get_download_url() delegates to ArchiveManager.get_download_url()."""
    workspace = _workspace()

    with patch("core.destinations.archive.backend.ArchiveManager") as mock_manager_cls:
        mock_manager = mock_manager_cls.return_value
        mock_manager.get_download_url.return_value = (
            "http://s3.example.com/ws.zip?token=abc"
        )

        backend = ArchiveDestinationBackend()
        url = backend.get_download_url(workspace)

    mock_manager.get_download_url.assert_called_once_with(workspace)
    assert url == "http://s3.example.com/ws.zip?token=abc"
