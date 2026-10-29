"""Tests for the working rows of a migration run."""

import os
from unittest.mock import MagicMock

import pytest

from core.backends.source import SourceFile, SourceFolder
from core.factories import WorkspaceFactory
from core.models import MigrationFile, MigrationRun
from core.processing.migration_run import (
    create_migration_run,
    delete_migration_run,
    download_migration_file,
    failed_downloads,
)
from core.processing.plan import build_plan

pytestmark = pytest.mark.django_db


def _source(file_id, name):
    return SourceFile(id=file_id, name=name, extension=".txt", download_url="x")


def _run_with_files(*sources):
    plan = build_plan(SourceFolder(name="root", files=list(sources)))
    return create_migration_run(WorkspaceFactory(), "task-1", plan)


def _backend(download):
    backend = MagicMock()
    backend.download_file.side_effect = download
    return backend


def _write(content):
    def download(_file, destination):
        with open(destination, "wb") as fh:
            fh.write(content)

    return download


def test_create_migration_run_stores_the_plan():
    """The run keeps the planned folders and one row per planned file, in order."""
    workspace = WorkspaceFactory()
    doc = _source("f1", "doc")
    notes = _source("f2", "notes")
    plan = build_plan(
        SourceFolder(
            name="root",
            files=[notes],
            children=[SourceFolder(name="sub", files=[doc]), SourceFolder(name="e")],
        )
    )

    run = create_migration_run(workspace, "task-1", plan)

    assert run.workspace == workspace
    assert run.task_id == "task-1"
    assert run.dirs == ["sub", "e"]
    assert run.remaining_files == 2
    assert [(row.position, row.source, row.path) for row in run.files.all()] == [
        (0, doc.to_dict(), os.path.join("sub", "doc.txt")),
        (1, notes.to_dict(), "notes.txt"),
    ]


def test_create_migration_run_stores_the_size_the_source_expects():
    """Each row keeps the size given by the source, None when it gives none."""
    sized = SourceFile(id="f1", name="a", extension=".txt", download_url="x", size=12)

    run = _run_with_files(sized, _source("f2", "b"))

    assert list(run.files.values_list("path", "expected_size")) == [
        ("a.txt", 12),
        ("b.txt", None),
    ]


def test_create_migration_run_stores_refused_files_as_failed():
    """A file refused by the plan gets a failed row, and is not left to
    download."""
    escaping = SourceFile(id="f2", name="..", extension="", download_url="x")
    plan = build_plan(SourceFolder(name="root", files=[_source("f1", "doc"), escaping]))

    run = create_migration_run(WorkspaceFactory(), "task-1", plan)

    assert run.remaining_files == 1
    refused = run.files.get(position=1)
    assert (refused.source, refused.path, refused.status) == (
        escaping.to_dict(),
        "..",
        MigrationFile.Status.DOWNLOAD_FAILED,
    )
    assert "is not inside" in refused.error


def test_create_migration_run_replaces_the_rows_of_a_retried_task():
    """A retried task, whose rows were left by a killed worker, starts over with
    the new plan only."""
    workspace = WorkspaceFactory()
    stale = create_migration_run(
        workspace,
        "task-1",
        build_plan(SourceFolder(name="root", files=[_source("f1", "old")])),
    )
    new = _source("f2", "new")

    run = create_migration_run(
        workspace, "task-1", build_plan(SourceFolder(name="root", files=[new]))
    )

    assert not MigrationRun.objects.filter(pk=stale.pk).exists()
    assert [row.source for row in MigrationFile.objects.all()] == [new.to_dict()]
    assert run.remaining_files == 1


def test_download_migration_file_records_the_size(tmp_path):
    """A downloaded file is recorded with its size on disk."""
    doc = _source("f1", "doc")
    migration_file = _run_with_files(doc).files.get()
    destination = str(tmp_path / "doc.txt")
    backend = _backend(_write(b"12345"))

    download_migration_file(migration_file, backend, destination)

    backend.download_file.assert_called_once_with(doc, destination)
    migration_file.refresh_from_db()
    assert (migration_file.status, migration_file.size, migration_file.error) == (
        MigrationFile.Status.DOWNLOADED,
        5,
        "",
    )


def test_download_migration_file_records_the_mime_type(tmp_path):
    """The type of a downloaded file is detected from its content."""
    migration_file = _run_with_files(_source("f1", "doc")).files.get()

    download_migration_file(
        migration_file, _backend(_write(b"%PDF-1.4\n")), str(tmp_path / "doc.txt")
    )

    migration_file.refresh_from_db()
    assert migration_file.mime_type == "application/pdf"


def test_download_migration_file_records_a_failure_without_raising(tmp_path):
    """A failed download is recorded with its error and leaves no partial file."""
    migration_file = _run_with_files(_source("f1", "doc")).files.get()
    destination = tmp_path / "doc.txt"

    def broken(_file, path):
        with open(path, "wb") as fh:
            fh.write(b"partial")
        raise RuntimeError("403 Forbidden")

    download_migration_file(migration_file, _backend(broken), str(destination))

    migration_file.refresh_from_db()
    assert (migration_file.status, migration_file.error) == (
        MigrationFile.Status.DOWNLOAD_FAILED,
        "403 Forbidden",
    )
    assert not destination.exists()


def test_download_migration_file_flags_a_download_that_wrote_nothing(tmp_path):
    """A download that returns without error but leaves no file is not a success."""
    migration_file = _run_with_files(_source("f1", "doc")).files.get()

    download_migration_file(
        migration_file, _backend(lambda _file, _path: None), str(tmp_path / "doc.txt")
    )

    migration_file.refresh_from_db()
    assert (migration_file.status, migration_file.size, migration_file.error) == (
        MigrationFile.Status.NOT_WRITTEN,
        None,
        "Download reported success but wrote no file",
    )


def test_download_migration_file_updates_the_given_instance(tmp_path):
    """The caller sees the outcome without reloading the row."""
    migration_file = _run_with_files(_source("f1", "doc")).files.get()

    download_migration_file(
        migration_file, _backend(_write(b"12")), str(tmp_path / "doc.txt")
    )

    assert (migration_file.status, migration_file.size) == (
        MigrationFile.Status.DOWNLOADED,
        2,
    )


def test_delete_migration_run_purges_the_run_and_its_files():
    """Once the run is over, nothing of its working state remains."""
    run = _run_with_files(_source("f1", "doc"))

    delete_migration_run(run.task_id)

    assert not MigrationRun.objects.exists()
    assert not MigrationFile.objects.exists()


def test_delete_migration_run_is_safe_when_there_is_none():
    """A task that failed before creating its run has nothing to purge."""
    delete_migration_run("unknown-task")  # must not raise


def test_failed_downloads_lists_the_files_that_did_not_reach_the_disk():
    """Refused, failed and unwritten files are listed in the plan order, as
    Workspace.download_errors keeps them."""
    escaping = SourceFile(id="f4", name="..", extension="", download_url="x")
    run = _run_with_files(
        _source("f1", "a"), _source("f2", "b"), _source("f3", "c"), escaping
    )
    run.files.filter(path="a.txt").update(status=MigrationFile.Status.DOWNLOADED)
    run.files.filter(path="b.txt").update(
        status=MigrationFile.Status.DOWNLOAD_FAILED, error="403"
    )
    run.files.filter(path="c.txt").update(
        status=MigrationFile.Status.NOT_WRITTEN, error="no file"
    )

    failed = failed_downloads(run)

    assert [error["id"] for error in failed] == ["f2", "f3", "f4"]
    assert failed[0] == {"id": "f2", "name": "b.txt", "path": "b.txt", "error": "403"}


def test_failed_downloads_is_empty_when_every_file_was_downloaded():
    """A run without loss has no download error."""
    run = _run_with_files(_source("f1", "a"))
    run.files.update(status=MigrationFile.Status.DOWNLOADED)

    assert not failed_downloads(run)
