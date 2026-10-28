"""Tests for ArchiveDestinationBackend."""

# pylint: disable=redefined-outer-name  # pytest fixtures intentionally shadow outer names

import json
from unittest.mock import patch

import pytest
from boto3.exceptions import S3UploadFailedError

from core.backends.destination import (
    AbstractDestinationBackend,
    FileExportStatus,
    RejectedFile,
)
from core.destinations.archive.backend import ArchiveDestinationBackend
from core.factories import UserFactory
from core.models import Workspace
from core.processing.file_exports import FileExports

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def mails_manager():
    """Prevent real MailsManager calls across the archive tests."""
    with patch("core.destinations.archive.backend.MailsManager") as mock_cls:
        yield mock_cls


@pytest.fixture(autouse=True)
def rejected_files_archive():
    """Capture what the export hands to the rejected files archive, which would
    otherwise reach S3."""
    with patch("core.destinations.archive.backend.RejectedFilesArchive") as mock_cls:
        yield mock_cls


@pytest.fixture(name="bucket")
def fixture_bucket():
    """In-memory S3 bucket behind ArchiveManager, as {key: bytes}."""
    objects = {}
    with patch("core.destinations.archive.backend.ArchiveManager") as mock_cls:
        manager = mock_cls.return_value

        def upload_file(path, key):
            with open(path, "rb") as f:
                objects[key] = f.read()

        def put_object(key, body):
            objects[key] = body

        def delete_prefix(prefix):
            for key in [k for k in objects if k.startswith(prefix + "/")]:
                del objects[key]

        manager.upload_file.side_effect = upload_file
        manager.put_object.side_effect = put_object
        manager.delete_prefix.side_effect = delete_prefix
        manager.delete_file.side_effect = lambda key: objects.pop(key, None)
        manager.objects = objects
        yield manager


@pytest.fixture(name="export_run")
def fixture_export_run(tmp_path):
    """Export a run to the archive file by file, as the export task does;
    return the failed destinations by name."""

    def export(run, user=None):
        exports = FileExports(run, user or UserFactory(), [ArchiveDestinationBackend()])
        exports.open()
        for migration_file in run.files.all():
            local_path = tmp_path / migration_file.path
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(f"content of {migration_file.path}".encode())
            exports.put(migration_file, str(local_path))
        return exports.close()

    return export


def _manifest(bucket, workspace):
    workspace.refresh_from_db()
    key = workspace.get_destination_metadata("archive")["manifest_key"]
    return json.loads(bucket.objects[key])


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


def test_exports_file_by_file():
    """The archive receives the run file by file, nothing is zipped on disk."""
    assert ArchiveDestinationBackend.exports_file_by_file is True


def test_put_stores_each_file_under_an_opaque_key(bucket, make_run, export_run):
    """Each file lands on S3 under a key made of ids only (file names may be
    personal data), inside the run's own prefix."""
    run = make_run(paths=("docs/report.pdf",))

    assert export_run(run) == {}

    migration_file = run.files.get()
    result = migration_file.results["archive"]
    assert result["status"] == FileExportStatus.SENT
    assert result["key"] == (
        f"archives/{run.workspace.id}/{run.id}/files/{migration_file.id}"
    )
    assert bucket.objects[result["key"]] == b"content of docs/report.pdf"


def test_put_marks_the_file_failed_when_s3_refuses_it(bucket, make_run):
    """An S3 error loses this file only: the export goes on with the next ones."""
    run = make_run(paths=("report.pdf",))
    backend = ArchiveDestinationBackend()
    backend.open_export(UserFactory(), run)
    bucket.upload_file.side_effect = S3UploadFailedError("S3 down")
    migration_file = run.files.get()

    backend.put_file(UserFactory(), run, migration_file, "/nowhere/report.pdf")

    assert migration_file.results["archive"] == {
        "status": FileExportStatus.FAILED,
        "error": "S3 down",
    }


def test_close_writes_the_manifest_of_the_archive(bucket, make_run, export_run):
    """The manifest lists the folders, empty ones included, and every stored
    file in the order of the plan, with its path in the archive, its key and
    its size."""
    run = make_run(paths=("a.txt", "docs/b.txt"), dirs=("empty",), size=5)

    export_run(run)

    assert _manifest(bucket, run.workspace) == {
        "dirs": ["empty", "docs"],
        "files": [
            {"path": row.path, "key": row.results["archive"]["key"], "size": 5}
            for row in run.files.all()
        ],
    }


def test_close_adds_the_members_listing(bucket, make_run, export_run):
    """The shared-users listing goes at the root of the archive, as before."""
    run = make_run(
        paths=("a.txt",),
        members=[{"name": "Dupont", "firstName": "Jean", "email": "jean@example.com"}],
    )

    export_run(run)

    entry = _manifest(bucket, run.workspace)["files"][-1]
    assert entry["path"] == "users_list_by_migrator.csv"
    assert bucket.objects[entry["key"]] == b"Dupont,Jean,jean@example.com\r\n"
    assert entry["size"] == len(bucket.objects[entry["key"]])


@pytest.mark.usefixtures("bucket")
def test_close_marks_the_archive_ready_and_mails_the_user(
    make_run, export_run, mails_manager, settings
):
    """The mail links the frontend download page, never a raw S3 URL."""
    settings.LOGIN_REDIRECT_URL = "http://localhost:3010"
    run = make_run(destination_statuses={"archive": Workspace.Status.PENDING})
    user = UserFactory()

    export_run(run, user=user)

    workspace = run.workspace
    workspace.refresh_from_db()
    assert workspace.get_destination_status("archive") == Workspace.Status.SUCCESS
    args, _ = mails_manager.return_value.send_migration_mail.call_args
    assert args[:3] == (user, run.workspace, "archive_download")
    assert args[3]["download_url"] == (
        f"http://localhost:3010/download-archive/?workspaceId={workspace.id}"
    )


def test_close_hands_the_failed_files_back_to_the_user(
    bucket, make_run, export_run, rejected_files_archive
):
    """A file S3 did not store is left out of the manifest and goes to the
    rejected files archive, from the local folder that kept it."""
    run = make_run(paths=("a.txt", "b.txt"))
    upload_file = bucket.upload_file.side_effect

    def fail_on_b(path, key):
        if path.endswith("b.txt"):
            raise S3UploadFailedError("S3 down")
        upload_file(path, key)

    bucket.upload_file.side_effect = fail_on_b

    assert export_run(run) == {}

    paths = [entry["path"] for entry in _manifest(bucket, run.workspace)["files"]]
    assert paths == ["a.txt"]
    rejected_files_archive.return_value.build.assert_called_once()
    _, lost = rejected_files_archive.return_value.build.call_args.args
    assert lost == [RejectedFile("b.txt", "upload_failed")]


def test_close_fails_when_no_file_was_stored(bucket, make_run, export_run):
    """An archive without any of the files is a failed export, and the
    previous archive stays downloadable."""
    run = make_run(paths=("a.txt",))
    run.workspace.set_destination_metadata("archive", {"s3_key": "previous.zip"})
    run.workspace.save()
    bucket.objects["previous.zip"] = b"zip"
    bucket.upload_file.side_effect = S3UploadFailedError("S3 down")

    failures = export_run(run)

    assert "archive" in failures
    run.workspace.refresh_from_db()
    assert run.workspace.get_destination_metadata("archive") == {
        "s3_key": "previous.zip"
    }
    assert set(bucket.objects) == {"previous.zip"}


def test_close_of_an_empty_workspace_still_makes_an_archive(
    bucket, make_run, export_run
):
    """A workspace without files gets an archive of its folders, as before."""
    run = make_run(paths=(), dirs=("empty",))

    assert export_run(run) == {}

    assert _manifest(bucket, run.workspace) == {"dirs": ["empty"], "files": []}


def test_close_deletes_the_previous_archive(bucket, make_run, export_run):
    """Only the last archive is kept: a new one replaces the previous one,
    whether it is a manifest or a zip of the former export."""
    run = make_run(paths=("a.txt",))
    previous = f"archives/{run.workspace.id}/previous-run"
    run.workspace.set_destination_metadata(
        "archive",
        {"manifest_key": f"{previous}/manifest.json", "s3_key": "legacy.zip"},
    )
    run.workspace.save()
    bucket.objects.update(
        {f"{previous}/manifest.json": b"{}", f"{previous}/files/1": b"1"}
    )
    bucket.objects["legacy.zip"] = b"zip"

    export_run(run)

    assert all(
        key.startswith(f"archives/{run.workspace.id}/{run.id}/")
        for key in bucket.objects
    )
    assert run.workspace.get_destination_metadata("archive") == {
        "manifest_key": f"archives/{run.workspace.id}/{run.id}/manifest.json"
    }


def test_close_keeps_the_new_archive_when_deleting_the_previous_fails(
    bucket, make_run, export_run
):
    """The previous archive is only unlinked when S3 fails to delete it."""
    run = make_run(paths=("a.txt",))
    run.workspace.set_destination_metadata("archive", {"s3_key": "previous.zip"})
    run.workspace.save()
    bucket.delete_file.side_effect = RuntimeError("S3 down")

    assert export_run(run) == {}

    run.workspace.refresh_from_db()
    assert run.workspace.get_destination_metadata("archive") == {
        "manifest_key": f"archives/{run.workspace.id}/{run.id}/manifest.json"
    }


def test_abort_deletes_what_the_run_stored(bucket, make_run, tmp_path):
    """A failed export leaves nothing on S3, and the previous archive stays."""
    run = make_run(paths=("a.txt",))
    bucket.objects["previous.zip"] = b"zip"
    backend = ArchiveDestinationBackend()
    backend.open_export(UserFactory(), run)
    local_path = tmp_path / "a.txt"
    local_path.write_bytes(b"a")
    backend.put_file(UserFactory(), run, run.files.get(), str(local_path))

    backend.abort_export(run)

    assert set(bucket.objects) == {"previous.zip"}


def test_a_mail_failing_after_the_archive_is_linked_keeps_it(
    bucket, make_run, export_run, mails_manager, rejected_files_archive
):
    """The workspace links the new archive and the previous one is gone: the
    abort that follows a failed mail keeps the archive downloadable."""
    run = make_run(paths=("a.txt",))
    # The real build saves the workspace, its metadata included.
    rejected_files_archive.return_value.build.side_effect = (
        lambda *_: run.workspace.save()
    )
    mails_manager.return_value.send_migration_mail.side_effect = OSError("SMTP down")

    assert list(export_run(run)) == ["archive"]

    manifest = _manifest(bucket, run.workspace)
    assert all(entry["key"] in bucket.objects for entry in manifest["files"])


def test_abort_before_open_does_nothing(bucket, make_run):
    """Nothing was stored when the export failed before opening."""
    ArchiveDestinationBackend().abort_export(make_run())

    bucket.delete_prefix.assert_not_called()


def test_describe_rejection_explains_a_failed_upload():
    """The CSV of the rejected files tells why a file is not in the archive."""
    reason = ArchiveDestinationBackend().describe_rejection("upload_failed")

    assert reason == "Fichier non enregistré dans l'archive (erreur d'envoi)"


def test_get_download_url_returns_presigned_url():
    """get_download_url() delegates to ArchiveManager.get_download_url()."""
    workspace = Workspace()

    with patch("core.destinations.archive.backend.ArchiveManager") as mock_manager_cls:
        mock_manager = mock_manager_cls.return_value
        mock_manager.get_download_url.return_value = (
            "http://s3.example.com/ws.zip?token=abc"
        )

        url = ArchiveDestinationBackend().get_download_url(workspace)

    mock_manager.get_download_url.assert_called_once_with(workspace)
    assert url == "http://s3.example.com/ws.zip?token=abc"
