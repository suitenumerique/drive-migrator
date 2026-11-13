"""Shared fixtures for the Drive destination tests."""

from unittest.mock import patch

import pytest

from core.destinations.drive.backend import DriveDestinationBackend
from core.factories import UserFactory
from core.processing.file_exports import FileExports


@pytest.fixture(autouse=True)
def mails_manager():
    """Prevent real MailsManager calls across the Drive tests."""
    with patch("core.destinations.drive.backend.MailsManager") as mock_cls:
        yield mock_cls


@pytest.fixture(autouse=True)
def rejected_files_archive():
    """Capture what the export hands to the rejected files archive, which would
    otherwise reach S3."""
    with patch("core.destinations.drive.backend.RejectedFilesArchive") as mock_cls:
        yield mock_cls


@pytest.fixture()
def drive_client(settings):
    """Service account Drive client: items get the client id, folders their title."""
    settings.DRIVE_AUTH_MODE = "service_account"
    with patch("core.destinations.drive.backend.DriveServiceAccountBackend") as cls:
        client = cls.return_value
        client.create_folder.return_value = {"id": "root-uuid"}
        client.create_subfolder.side_effect = lambda title, parent_id: {
            "id": f"folder-{title}"
        }
        client.create_file_item.side_effect = lambda title, parent_id, size, item_id: {
            "id": item_id,
            "policy": f"https://s3.example.com/{title}",
        }
        client.list_children.return_value = []
        client.get_item.return_value = None
        yield client


@pytest.fixture(name="user")
def fixture_user():
    return UserFactory()


@pytest.fixture()
def migrate(tmp_path, user):
    """Export a run to Drive file by file, as the export task does; return the
    failed destinations by name."""

    def run_migration(run, destination=None, migration_user=None):
        destination = destination or DriveDestinationBackend()
        exports = FileExports(run, migration_user or user, [destination])
        exports.open()
        for migration_file in run.files.all():
            local_path = tmp_path / migration_file.path
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(b"x" * migration_file.size)
            exports.put(migration_file, str(local_path))
        return exports.close()

    return run_migration
