"""Tests for the working rows of a migration run."""

import os
from types import SimpleNamespace

import pytest

from core.backends.source import SourceFile, SourceFolder
from core.factories import WorkspaceFactory
from core.models import MigrationFile, MigrationRun
from core.processing.migration_run import (
    create_migration_run,
    delete_migration_run,
    record_retrieval,
)
from core.processing.plan import build_plan

pytestmark = pytest.mark.django_db


def _source(file_id, name):
    return SourceFile(id=file_id, name=name, extension=".txt", download_url="x")


def _run_with_files(*sources):
    plan = build_plan(SourceFolder(name="root", files=list(sources)))
    return create_migration_run(WorkspaceFactory(), "task-1", plan)


def _creator(written=None, failed=None, not_written=None):
    """A FolderCreator after the download, as seen by record_retrieval()."""
    return SimpleNamespace(
        written_files=written or {},
        failed_files=failed or [],
        not_written_ids=not_written or set(),
    )


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


def test_record_retrieval_stores_each_download_outcome(tmp_path):
    """Downloaded files get their size, lost ones their status and error."""
    (tmp_path / "ok.txt").write_bytes(b"12345")
    run = _run_with_files(
        _source("ok", "ok"),
        _source("failed", "failed"),
        _source("ghost", "ghost"),
        _source("skipped", "skipped"),
    )
    creator = _creator(
        written={"ok": "ok.txt"},
        failed=[
            {"id": "failed", "error": "403 Forbidden"},
            {"id": "ghost", "error": "Download reported success but wrote no file"},
        ],
        not_written={"ghost"},
    )

    record_retrieval(run, creator, str(tmp_path))

    rows = {row.source["id"]: row for row in run.files.all()}
    assert (rows["ok"].status, rows["ok"].size, rows["ok"].error) == (
        MigrationFile.Status.DOWNLOADED,
        5,
        "",
    )
    assert (rows["failed"].status, rows["failed"].error) == (
        MigrationFile.Status.DOWNLOAD_FAILED,
        "403 Forbidden",
    )
    assert rows["ghost"].status == MigrationFile.Status.NOT_WRITTEN
    assert rows["skipped"].status == MigrationFile.Status.PENDING


def test_delete_migration_run_purges_the_run_and_its_files():
    """Once the run is over, nothing of its working state remains."""
    run = _run_with_files(_source("f1", "doc"))

    delete_migration_run(run.task_id)

    assert not MigrationRun.objects.exists()
    assert not MigrationFile.objects.exists()


def test_delete_migration_run_is_safe_when_there_is_none():
    """A task that failed before creating its run has nothing to purge."""
    delete_migration_run("unknown-task")  # must not raise
