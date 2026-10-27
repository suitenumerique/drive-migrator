"""Shared fixtures for the Drive destination tests."""

# pylint: disable=redefined-outer-name  # pytest fixtures intentionally shadow outer names

import os
import uuid
from unittest.mock import patch

import pytest

from core.backends.source import SourceFile, SourceFolder
from core.destinations.drive.backend import DriveDestinationBackend
from core.factories import UserFactory, WorkspaceFactory
from core.models import MigrationFile
from core.processing.file_exports import FileExports
from core.processing.migration_run import create_migration_run
from core.processing.plan import build_plan


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


def _folder(paths, dirs):
    """The source tree holding the given file paths and (empty) folder paths."""
    root = SourceFolder(name="root")
    nodes = {"": root}

    def node(path):
        if path not in nodes:
            parent, name = os.path.split(path)
            nodes[path] = SourceFolder(name=name)
            node(parent).children.append(nodes[path])
        return nodes[path]

    for directory in dirs:
        node(directory)
    for path in paths:
        folder, filename = os.path.split(path)
        name, extension = os.path.splitext(filename)
        node(folder).files.append(
            SourceFile(id=path, name=name, extension=extension, download_url="x")
        )
    return root


@pytest.fixture()
def make_run():
    """Build the run of a workspace whose files are all downloaded."""

    def make(paths=("report.pdf",), dirs=(), size=3, **workspace_fields):
        workspace_fields.setdefault("title", "My Workspace")
        run = create_migration_run(
            WorkspaceFactory(**workspace_fields),
            str(uuid.uuid4()),
            build_plan(_folder(paths, dirs)),
        )
        run.files.update(status=MigrationFile.Status.DOWNLOADED, size=size)
        return run

    return make


@pytest.fixture()
def user():
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
