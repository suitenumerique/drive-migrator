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
    assert [
        (row.position, SourceFile.from_dict(row.source), row.path)
        for row in run.files.all()
    ] == [
        (0, doc, os.path.join("sub", "doc.txt")),
        (1, notes, "notes.txt"),
    ]


def test_create_migration_run_stores_refused_files_as_failed():
    """A file refused by the plan gets a failed row, and is not left to
    download."""
    escaping = SourceFile(id="f2", name="..", extension="", download_url="x")
    plan = build_plan(SourceFolder(name="root", files=[_source("f1", "doc"), escaping]))

    run = create_migration_run(WorkspaceFactory(), "task-1", plan)

    assert run.remaining_files == 1
    refused = run.files.get(position=1)
    assert (SourceFile.from_dict(refused.source), refused.path, refused.status) == (
        escaping,
        "..",
        MigrationFile.Status.DOWNLOAD_FAILED,
    )
    assert "is not inside" in refused.error


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
