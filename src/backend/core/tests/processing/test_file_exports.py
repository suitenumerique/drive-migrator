"""Tests for the destinations that receive a run file by file."""

# pylint: disable=redefined-outer-name  # pytest fixtures intentionally shadow outer names

from unittest.mock import MagicMock

import pytest

from core.backends.destination import FileExportStatus
from core.backends.source import SourceFile, SourceFolder
from core.factories import UserFactory, WorkspaceFactory
from core.processing.file_exports import FileExports
from core.processing.migration_run import create_migration_run
from core.processing.plan import build_plan

pytestmark = pytest.mark.django_db


@pytest.fixture()
def user():
    return UserFactory()


@pytest.fixture()
def run():
    files = [
        SourceFile(id=f"f{i}", name=f"doc{i}", extension=".txt", download_url="x")
        for i in range(4)
    ]
    plan = build_plan(SourceFolder(name="root", files=files))
    return create_migration_run(WorkspaceFactory(), "task-1", plan)


def _destination(name, statuses=None):
    """A file-by-file destination recording each file with the next status."""
    destination = MagicMock()
    destination.name = name
    statuses = iter(statuses or [])

    def put_file(_user, _run, migration_file, _local_path):
        migration_file.results[name] = {"status": next(statuses, FileExportStatus.SENT)}

    destination.put_file.side_effect = put_file
    return destination


def _exports(run, user, *destinations):
    exports = FileExports(run, user, list(destinations))
    exports.open()
    return exports


def _put_all(exports, run):
    for migration_file in run.files.all():
        exports.put(migration_file, f"/tmp/{migration_file.path}")


def test_open_prepares_every_destination(run, user):
    """Each destination is opened once, before any file."""
    drive = _destination("drive")

    _exports(run, user, drive)

    drive.open_export.assert_called_once_with(user, run)


def test_put_hands_the_file_to_every_destination_and_saves_results(run, user):
    """Every destination receives the file; their results land on its row."""
    drive, other = _destination("drive"), _destination("other")
    exports = _exports(run, user, drive, other)
    migration_file = run.files.first()

    exports.put(migration_file, "/tmp/doc0.txt")

    drive.put_file.assert_called_once_with(user, run, migration_file, "/tmp/doc0.txt")
    other.put_file.assert_called_once()
    migration_file.refresh_from_db()
    assert migration_file.results == {
        "drive": {"status": FileExportStatus.SENT},
        "other": {"status": FileExportStatus.SENT},
    }


def test_a_destination_that_raises_is_disabled_without_stopping_the_others(run, user):
    """After an error, the destination gets no more files; the others do."""
    drive, other = _destination("drive"), _destination("other")
    drive.put_file.side_effect = RuntimeError("401 Unauthorized")
    exports = _exports(run, user, drive, other)

    _put_all(exports, run)
    failures = exports.close()

    assert drive.put_file.call_count == 1
    assert other.put_file.call_count == 4
    drive.close_export.assert_not_called()
    drive.abort_export.assert_called_once_with(run)
    other.close_export.assert_called_once_with(user, run)
    assert list(failures) == ["drive"]
    assert str(failures["drive"]) == "401 Unauthorized"


def test_consecutive_file_failures_disable_the_destination(run, user, settings):
    """K files failing in a row mean the destination itself is unusable."""
    settings.MIGRATION_DESTINATION_MAX_CONSECUTIVE_FAILURES = 2
    failed = FileExportStatus.FAILED
    drive = _destination("drive", [failed, failed, failed, failed])
    exports = _exports(run, user, drive)

    _put_all(exports, run)

    assert drive.put_file.call_count == 2
    assert "drive" in exports.close()


def test_a_success_resets_the_consecutive_failures(run, user, settings):
    """Isolated failures are recorded on their files, not held against the destination."""
    settings.MIGRATION_DESTINATION_MAX_CONSECUTIVE_FAILURES = 2
    failed, sent = FileExportStatus.FAILED, FileExportStatus.SENT
    drive = _destination("drive", [failed, sent, failed, sent])
    exports = _exports(run, user, drive)

    _put_all(exports, run)

    assert drive.put_file.call_count == 4
    assert exports.close() == {}


def test_rejected_files_do_not_count_as_failures(run, user, settings):
    """A file refused by the destination says nothing about the destination."""
    settings.MIGRATION_DESTINATION_MAX_CONSECUTIVE_FAILURES = 2
    drive = _destination("drive", [FileExportStatus.REJECTED] * 4)
    exports = _exports(run, user, drive)

    _put_all(exports, run)

    assert drive.put_file.call_count == 4


def test_a_destination_that_fails_to_open_gets_no_file(run, user):
    """A destination that can't even be prepared is disabled from the start."""
    drive, other = _destination("drive"), _destination("other")
    drive.open_export.side_effect = RuntimeError("Drive down")
    exports = _exports(run, user, drive, other)

    _put_all(exports, run)

    drive.put_file.assert_not_called()
    assert other.put_file.call_count == 4
    assert list(exports.close()) == ["drive"]


def test_a_destination_that_fails_to_close_is_aborted(run, user):
    """An error while closing fails the destination like any other."""
    drive = _destination("drive")
    drive.close_export.side_effect = RuntimeError("All files rejected")
    exports = _exports(run, user, drive)

    failures = exports.close()

    drive.abort_export.assert_called_once_with(run)
    assert str(failures["drive"]) == "All files rejected"


def test_an_error_while_aborting_is_swallowed(run, user):
    """abort_export() is best effort: its own error never hides the first one."""
    drive = _destination("drive")
    drive.put_file.side_effect = RuntimeError("401 Unauthorized")
    drive.abort_export.side_effect = RuntimeError("still down")
    exports = _exports(run, user, drive)

    exports.put(run.files.first(), "/tmp/doc0.txt")

    assert str(exports.close()["drive"]) == "401 Unauthorized"


def test_abort_only_aborts_destinations_not_closed_yet(run, user):
    """After a fatal error of the run, every open destination is aborted once."""
    drive, other = _destination("drive"), _destination("other")
    exports = _exports(run, user, drive, other)

    exports.abort()

    drive.abort_export.assert_called_once_with(run)
    other.abort_export.assert_called_once_with(run)
    drive.close_export.assert_not_called()
