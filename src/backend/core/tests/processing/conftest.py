"""Fixtures of the export task tests, which ask for them with
pytest.mark.usefixtures: other tests keep the real code they patch."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.models import MigrationRun


class _FakeRun(SimpleNamespace):
    """A run kept out of the database, stopped as the model is."""

    stopped = MigrationRun.stopped


@pytest.fixture(name="work_directory")
def fixture_work_directory(settings, tmp_path):
    """Keep export task filesystem operations inside a temporary directory."""
    settings.APP_WORK_DIR = str(tmp_path)


@pytest.fixture(name="rejected_files_bucket")
def fixture_rejected_files_bucket():
    """Keep the rejected files cleanup away from S3."""
    with patch("core.processing.rejected_files.ArchiveManager"):
        yield


@pytest.fixture(name="migration_run")
def fixture_migration_run():
    """Keep the run's working rows out of the database: workspaces are mocks here."""
    with (
        patch("core.processing.tasks.run_exists", return_value=False),
        patch("core.processing.tasks.claim_export", return_value=True),
        patch("core.processing.tasks.mark_run_alive"),
        patch("core.processing.tasks.TaskResult"),
        patch("core.processing.tasks._reported", return_value=False),
        patch("core.processing.tasks.create_migration_run") as create,
        patch("core.processing.tasks.delete_migration_run") as delete,
        patch("core.processing.tasks.save_run_files_stats") as save_stats,
        patch("core.processing.tasks.drop_unlinked_kept_files") as drop_kept,
        patch("core.processing.tasks.failed_downloads", return_value=[]) as failed,
        patch("core.processing.tasks.stop_run") as stop,
        patch("core.processing.tasks.record_file_error"),
        patch("core.processing.tasks._extra_task_of", return_value=None),
        patch("core.processing.tasks._record_outcome"),
    ):
        files = MagicMock()
        files.count.return_value = 0
        create.side_effect = lambda workspace, task_id, plan: _FakeRun(
            pk="run-1",
            task_id=task_id,
            workspace=workspace,
            exports_state={},
            integrity_state={},
            source_token_expired=False,
            stalled=False,
            files=files,
        )
        stop.side_effect = lambda run: setattr(run, "source_token_expired", True)
        yield SimpleNamespace(
            create=create,
            delete=delete,
            save_stats=save_stats,
            drop_kept=drop_kept,
            failed=failed,
            files=files,
        )
