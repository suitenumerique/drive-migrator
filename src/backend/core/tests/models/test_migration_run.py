"""Tests for the MigrationRun and MigrationFile working models."""

from django.core.exceptions import ValidationError

import pytest

from core.factories import WorkspaceFactory
from core.models import MigrationFile, MigrationRun

pytestmark = pytest.mark.django_db


def _run(task_id="task-1"):
    return MigrationRun.objects.create(workspace=WorkspaceFactory(), task_id=task_id)


def _file(run, position=0):
    return MigrationFile.objects.create(
        run=run,
        position=position,
        source={"id": "f1", "name": "doc", "extension": ".pdf"},
        path="doc.pdf",
    )


def test_migration_file_starts_pending():
    """A planned file has not been downloaded nor sent anywhere yet."""
    migration_file = _file(_run())

    assert migration_file.status == MigrationFile.Status.PENDING
    assert migration_file.size is None
    assert migration_file.results == {}
    assert migration_file.finished_at is None


def test_migration_run_starts_empty():
    """A new run has no planned folder, no file left and was not finished."""
    run = _run()

    assert run.dirs == []
    assert run.remaining_files == 0
    assert run.finish_published is False
    assert run.destinations_state == {}


def test_migration_file_position_is_unique_within_a_run():
    """Two files of one run can't share a position in the plan."""
    run = _run()
    _file(run, position=0)

    with pytest.raises(ValidationError):
        _file(run, position=0)


def test_migration_run_files_are_ordered_by_position():
    """A run lists its files in plan order."""
    run = _run()
    second = _file(run, position=1)
    first = _file(run, position=0)

    assert list(run.files.all()) == [first, second]


def test_deleting_a_run_deletes_its_files():
    """Purging a finished run purges its working rows."""
    run = _run()
    _file(run)

    run.delete()

    assert not MigrationFile.objects.exists()


def test_deleting_the_workspace_deletes_its_runs():
    """A run lives no longer than its workspace."""
    run = _run()

    run.workspace.delete()

    assert not MigrationRun.objects.exists()


def test_migration_run_task_id_is_unique():
    """A Celery task drives a single run."""
    _run(task_id="task-1")

    with pytest.raises(ValidationError):
        _run(task_id="task-1")
