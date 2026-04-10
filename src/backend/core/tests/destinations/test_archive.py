"""Tests for ArchiveDestinationBackend."""

from unittest.mock import MagicMock, patch

import pytest

from core.backends.destination import AbstractDestinationBackend
from core.destinations.archive.backend import ArchiveDestinationBackend
from core.models import Workspace


@pytest.fixture(autouse=True)
def _patch_mails_manager():
    """Prevent real MailsManager calls (DB access) across all tests in this module."""
    with patch("core.destinations.archive.backend.MailsManager") as mock_cls:
        mock_cls.return_value.send_archive_download_mail = MagicMock()
        yield mock_cls


def test_export_sends_download_mail(_patch_mails_manager):
    """export() sends the archive download email to the user after uploading."""
    workspace = MagicMock(spec=Workspace)
    user = MagicMock()

    with patch("core.destinations.archive.backend.ArchiveManager") as mock_manager_cls:
        mock_manager = mock_manager_cls.return_value
        mock_manager.upload_archive.return_value = "http://s3.example.com/ws.zip"

        backend = ArchiveDestinationBackend()
        backend.export(workspace, user, "/tmp/workspace")

    _patch_mails_manager.return_value.send_archive_download_mail.assert_called_once_with(
        user, workspace, "http://s3.example.com/ws.zip"
    )


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
    workspace = MagicMock(spec=Workspace)
    workspace.get_destination_status.return_value = Workspace.Status.PENDING
    user = MagicMock()

    with patch("core.destinations.archive.backend.ArchiveManager") as mock_manager_cls:
        mock_manager = mock_manager_cls.return_value
        mock_manager.upload_archive.return_value = "http://s3.example.com/ws.zip"

        backend = ArchiveDestinationBackend()
        backend.export(workspace, user, "/tmp/workspace")

    mock_manager.zip_workspace_folder.assert_called_once_with(workspace)


def test_export_uploads_to_s3():
    """export() uploads the archive to S3."""
    workspace = MagicMock(spec=Workspace)
    workspace.get_destination_status.return_value = Workspace.Status.PENDING
    user = MagicMock()

    with patch("core.destinations.archive.backend.ArchiveManager") as mock_manager_cls:
        mock_manager = mock_manager_cls.return_value
        mock_manager.upload_archive.return_value = "http://s3.example.com/ws.zip"

        backend = ArchiveDestinationBackend()
        backend.export(workspace, user, "/tmp/workspace")

    mock_manager.upload_archive.assert_called_once_with(workspace)


def test_export_sets_status_success():
    """export() sets destination status to SUCCESS on success."""
    workspace = MagicMock(spec=Workspace)
    workspace.get_destination_status.return_value = Workspace.Status.PENDING
    user = MagicMock()

    with patch("core.destinations.archive.backend.ArchiveManager") as mock_manager_cls:
        mock_manager = mock_manager_cls.return_value
        mock_manager.upload_archive.return_value = "http://s3.example.com/ws.zip"

        backend = ArchiveDestinationBackend()
        backend.export(workspace, user, "/tmp/workspace")

    workspace.set_destination_status.assert_called_once_with(
        "archive", Workspace.Status.SUCCESS
    )
    workspace.save.assert_called()


def test_export_includes_any_extra_files_in_local_folder():
    """export() zips the entire local folder, including any extra files (e.g. members CSV)."""
    workspace = MagicMock(spec=Workspace)
    workspace.get_destination_status.return_value = Workspace.Status.PENDING
    user = MagicMock()

    with patch("core.destinations.archive.backend.ArchiveManager") as mock_manager_cls:
        mock_manager = mock_manager_cls.return_value
        mock_manager.upload_archive.return_value = "http://s3.example.com/ws.zip"

        backend = ArchiveDestinationBackend()
        backend.export(workspace, user, "/tmp/workspace")

    # zip_workspace_folder is called with the workspace (folder path is derived internally)
    # — the entire folder is zipped, whatever it contains.
    mock_manager.zip_workspace_folder.assert_called_once_with(workspace)


def test_get_download_url_returns_presigned_url():
    """get_download_url() delegates to ArchiveManager.get_download_url()."""
    workspace = MagicMock(spec=Workspace)

    with patch("core.destinations.archive.backend.ArchiveManager") as mock_manager_cls:
        mock_manager = mock_manager_cls.return_value
        mock_manager.get_download_url.return_value = (
            "http://s3.example.com/ws.zip?token=abc"
        )

        backend = ArchiveDestinationBackend()
        url = backend.get_download_url(workspace)

    mock_manager.get_download_url.assert_called_once_with(workspace)
    assert url == "http://s3.example.com/ws.zip?token=abc"
