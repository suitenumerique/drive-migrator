"""Tests for the tasks carrying on a run once export() planned it: one task per
file, dispatched batch by batch, then the finish of the run."""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import pytest
from celery import states
from django_celery_results.models import TaskResult

from core.backends.source import SourceFile, SourceFolder, SourceTokenExpired
from core.factories import UserFactory, WorkspaceFactory
from core.models import MigrationFile, MigrationRun
from core.processing import tasks
from core.processing.migration_run import create_migration_run
from core.processing.plan import build_plan

pytestmark = pytest.mark.django_db


@pytest.fixture(name="published")
def fixture_published():
    """The tasks published, as (task name, *args), instead of queued."""
    published = []

    def recorder(name):
        return lambda *args: published.append((name, *args))

    with (
        patch.object(tasks.export_file, "delay", side_effect=recorder("export_file")),
        patch.object(
            tasks.finish_migration, "delay", side_effect=recorder("finish_migration")
        ),
    ):
        yield published


@pytest.fixture(name="user")
def fixture_user():
    return UserFactory()


@pytest.fixture(name="source_backend")
def fixture_source_backend():
    with patch("core.processing.tasks.SourceManager") as manager_cls:
        yield manager_cls.return_value.get_backend.return_value


@pytest.fixture(name="export_one")
def fixture_export_one():
    """What exporting a file does is tested with export(): only the wiring here."""
    with patch("core.processing.tasks._export_file") as export_one:
        yield export_one


@pytest.fixture(name="committed")
def fixture_committed(django_capture_on_commit_callbacks):
    """Run the on_commit callbacks, as the end of a real transaction would."""
    return lambda: django_capture_on_commit_callbacks(execute=True)


@pytest.fixture(name="worker")
def fixture_worker(published, user, source_backend, export_one, committed):
    """What a file task works with, and what it publishes."""
    return SimpleNamespace(
        published=published,
        user=user,
        source_backend=source_backend,
        export_one=export_one,
        committed=committed,
    )


def _run(files_count, max_files, settings):
    settings.MIGRATION_BATCH_MAX_FILES = max_files
    settings.MIGRATION_BATCH_MAX_BYTES = 0
    files = [
        SourceFile(id=f"f{i}", name=f"doc{i}", extension=".txt", download_url="x")
        for i in range(files_count)
    ]
    plan = build_plan(SourceFolder(name="root", files=files))
    return create_migration_run(WorkspaceFactory(), "task-1", plan)


def _ids(run, user):
    return str(run.pk), str(user.pk)


def _file_task(run, user, position):
    return ("export_file", *_ids(run, user), position)


def _finish_task(run, user):
    return ("finish_migration", *_ids(run, user))


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def test_an_export_delivered_twice_leaves_its_run_alone(settings, user, source_backend):
    """A second delivery of export(), by the broker, touches neither the run
    already started nor its workspace."""
    run = _run(2, 0, settings)
    workspace = run.workspace
    workspace.download_errors = [{"name": "a.txt"}]
    workspace.save()
    data = {"workspace": {"id": str(workspace.id)}, "user": {"id": str(user.id)}}

    result = tasks.export.apply(args=[data], task_id="task-1")

    assert result.state == "IGNORED"

    assert MigrationRun.objects.filter(pk=run.pk).exists()
    source_backend.begin_export.assert_not_called()
    source_backend.finalize_export.assert_not_called()
    workspace.refresh_from_db()
    assert workspace.download_errors == [{"name": "a.txt"}]


# ---------------------------------------------------------------------------
# dispatch_batch
# ---------------------------------------------------------------------------


def test_dispatch_batch_publishes_a_task_per_file_of_the_batch(
    settings, published, user, committed
):
    """Each file of the next batch gets its own task."""
    run = _run(3, 2, settings)

    with committed():
        tasks.dispatch_batch(run, user)

    assert published == [_file_task(run, user, 0), _file_task(run, user, 1)]


def test_dispatch_batch_publishes_once_the_transaction_commits(
    settings, published, user, django_capture_on_commit_callbacks
):
    """A worker must never read a run not committed yet."""
    run = _run(2, 0, settings)

    with django_capture_on_commit_callbacks() as callbacks:
        tasks.dispatch_batch(run, user)

    assert not published
    assert len(callbacks) == 2


def test_dispatch_batch_publishes_the_finish_of_a_run_without_files(
    settings, published, user, committed
):
    """A run with nothing to download goes straight to its finish, only once."""
    run = _run(0, 0, settings)

    with committed():
        tasks.dispatch_batch(run, user)
        tasks.dispatch_batch(run, user)

    assert published == [_finish_task(run, user)]
    run.refresh_from_db()
    assert run.finish_published


# ---------------------------------------------------------------------------
# export_file
# ---------------------------------------------------------------------------


def test_export_file_exports_its_file_and_counts_it_done(settings, worker):
    """The task resumes the source, exports its file, then counts it done."""
    run = _run(3, 2, settings)
    with worker.committed():
        tasks.dispatch_batch(run, worker.user)
        tasks.export_file(*_ids(run, worker.user), 0)

    worker.source_backend.resume_export.assert_called_once_with(run.workspace)
    worker.export_one.assert_called_once_with(
        ANY, worker.user, worker.source_backend, ANY
    )
    assert worker.export_one.call_args.args[0].pk == run.pk
    assert worker.export_one.call_args.args[3].position == 0
    assert run.files.get(position=0).finished_at is not None
    assert run.files.get(position=1).finished_at is None
    run.refresh_from_db()
    assert (run.in_flight, run.remaining_files) == (1, 2)
    assert len(worker.published) == 2


def test_the_last_file_of_a_batch_dispatches_the_next_batch(settings, worker):
    """Once every file of a batch is done, the next batch is queued."""
    run = _run(3, 2, settings)
    with worker.committed():
        tasks.dispatch_batch(run, worker.user)
        tasks.export_file(*_ids(run, worker.user), 0)
        tasks.export_file(*_ids(run, worker.user), 1)

    assert worker.published[2:] == [_file_task(run, worker.user, 2)]


def test_the_last_file_of_the_run_publishes_its_finish(settings, worker):
    """Once every file of the run is done, its finish is queued, only once."""
    run = _run(2, 0, settings)
    with worker.committed():
        tasks.dispatch_batch(run, worker.user)
        tasks.export_file(*_ids(run, worker.user), 0)
        tasks.export_file(*_ids(run, worker.user), 1)

    assert worker.published[2:] == [_finish_task(run, worker.user)]
    run.refresh_from_db()
    assert (run.in_flight, run.remaining_files) == (0, 0)


def test_an_expired_source_token_stops_the_run(settings, worker):
    """Every file left would fail the same way: the files already queued are
    skipped, no batch follows, and the last of them queues the finish."""
    run = _run(3, 2, settings)
    worker.export_one.side_effect = SourceTokenExpired("token expired")
    with worker.committed():
        tasks.dispatch_batch(run, worker.user)
        tasks.export_file(*_ids(run, worker.user), 0)
        tasks.export_file(*_ids(run, worker.user), 1)

    assert worker.export_one.call_count == 1
    assert worker.published[2:] == [_finish_task(run, worker.user)]
    run.refresh_from_db()
    assert run.source_token_expired


def test_an_unexpected_error_is_kept_on_the_file_and_the_run_goes_on(settings, worker):
    """One file failing must not block the run: its error stays on its row."""
    run = _run(2, 0, settings)
    worker.export_one.side_effect = [OSError("disk full"), None]
    with worker.committed():
        tasks.dispatch_batch(run, worker.user)
        tasks.export_file(*_ids(run, worker.user), 0)
        tasks.export_file(*_ids(run, worker.user), 1)

    failed = run.files.get(position=0)
    assert failed.status == MigrationFile.Status.DOWNLOAD_FAILED
    assert failed.error == "disk full"
    assert failed.finished_at is not None
    assert worker.published[2:] == [_finish_task(run, worker.user)]


def test_an_unexpected_error_after_the_download_keeps_the_file_downloaded(
    settings, worker
):
    """A file already on disk keeps its status: only the error is added."""
    run = _run(1, 0, settings)

    def fail_after_download(_run, _user, _source, migration_file):
        MigrationFile.objects.filter(pk=migration_file.pk).update(
            status=MigrationFile.Status.DOWNLOADED
        )
        migration_file.status = MigrationFile.Status.DOWNLOADED
        raise RuntimeError("handover failed")

    worker.export_one.side_effect = fail_after_download
    with worker.committed():
        tasks.dispatch_batch(run, worker.user)
        tasks.export_file(*_ids(run, worker.user), 0)

    row = run.files.get()
    assert row.status == MigrationFile.Status.DOWNLOADED
    assert row.error == "handover failed"


@pytest.mark.usefixtures("source_backend")
def test_export_file_of_a_purged_run_does_nothing(published, user, export_one):
    """A file task left in the queue after its run was purged just ends."""
    tasks.export_file(str(uuid.uuid4()), str(user.pk), 0)

    export_one.assert_not_called()
    assert not published


# ---------------------------------------------------------------------------
# finish_migration
# ---------------------------------------------------------------------------


@pytest.fixture(name="finish_steps")
def fixture_finish_steps(source_backend):
    """The steps of the finish, recorded in order; the extra task found."""
    order = []
    extra_task = MagicMock()
    with (
        patch("core.processing.tasks._finish_export") as finish_export,
        patch("core.processing.tasks._abort_export") as abort,
        patch("core.processing.tasks._clean_up_run") as clean_up,
        patch("core.processing.tasks.IntegrityTracker") as tracker_cls,
        patch("core.processing.tasks._extra_task_of", return_value=extra_task),
        patch("core.processing.tasks.on_export_success") as on_success,
        patch("core.processing.tasks.on_export_failure") as on_failure,
    ):
        clean_up.side_effect = lambda *args: order.append("clean_up")
        source_backend.finalize_export.side_effect = lambda *args: order.append(
            "finalize"
        )
        on_success.side_effect = lambda *args: order.append("success")
        on_failure.side_effect = lambda *args: order.append("failure")
        yield SimpleNamespace(
            order=order,
            extra_task=extra_task,
            finish_export=finish_export,
            abort=abort,
            clean_up=clean_up,
            tracker=tracker_cls.from_run.return_value,
            on_success=on_success,
            on_failure=on_failure,
        )


def test_finish_migration_closes_purges_then_reports_a_success(
    settings, user, source_backend, finish_steps
):
    """The destinations close, the run is purged, the source released, then the
    success is reported."""
    run = _run(1, 0, settings)

    tasks.finish_migration(*_ids(run, user))

    source_backend.resume_export.assert_called_once_with(run.workspace)
    finish_steps.finish_export.assert_called_once_with(ANY, user)
    finish_steps.clean_up.assert_called_once_with(
        "task-1", ANY, finish_steps.tracker, False
    )
    finish_steps.on_success.assert_called_once_with(finish_steps.extra_task)
    assert finish_steps.order == ["clean_up", "finalize", "success"]


def test_finish_migration_reports_a_failure_then_raises_it(
    settings, user, finish_steps
):
    """A failed run is purged and the source released all the same; the error
    reaches the user, then the logs."""
    run = _run(1, 0, settings)
    error = RuntimeError("Export failed for drive")
    finish_steps.finish_export.side_effect = error

    with pytest.raises(RuntimeError, match="drive"):
        tasks.finish_migration(*_ids(run, user))

    finish_steps.clean_up.assert_called_once_with(
        "task-1", ANY, finish_steps.tracker, True
    )
    finish_steps.on_failure.assert_called_once_with(finish_steps.extra_task, error)
    assert finish_steps.order == ["clean_up", "finalize", "failure"]


def test_finish_migration_of_a_stopped_run_asks_to_log_in_again(
    settings, user, finish_steps
):
    """A run stopped by an expired source token closes no destination: they
    are aborted, and the user is asked to log in again."""
    run = _run(1, 0, settings)
    MigrationRun.objects.filter(pk=run.pk).update(source_token_expired=True)

    with pytest.raises(SourceTokenExpired):
        tasks.finish_migration(*_ids(run, user))

    finish_steps.finish_export.assert_not_called()
    finish_steps.abort.assert_called_once_with(ANY, user)
    extra_task, error = finish_steps.on_failure.call_args.args
    assert extra_task is finish_steps.extra_task
    assert isinstance(error, SourceTokenExpired)


def test_finish_migration_without_extra_task_reports_nothing(
    settings, user, finish_steps
):
    """A run whose task has no ExtraTaskInfo has nobody to report to."""
    run = _run(1, 0, settings)

    with patch("core.processing.tasks._extra_task_of", return_value=None):
        tasks.finish_migration(*_ids(run, user))

    finish_steps.on_success.assert_not_called()


@pytest.fixture(name="task_result")
def fixture_task_result():
    """The TaskResult of the migration, left pending by export()."""
    return TaskResult.objects.create(
        task_id="task-1", task_name=tasks.export.name, status=states.PENDING
    )


@pytest.mark.usefixtures("finish_steps")
def test_finish_migration_records_the_success_of_the_migration(
    settings, user, task_result
):
    """The admin shows the migration done once its run is."""
    run = _run(1, 0, settings)

    tasks.finish_migration(*_ids(run, user))

    task_result.refresh_from_db()
    assert task_result.status == states.SUCCESS
    assert task_result.date_done is not None
    assert task_result.result == "true"
    assert task_result.task_name == tasks.export.name


def test_finish_migration_records_the_failure_of_the_migration(
    settings, user, task_result, finish_steps
):
    """The admin shows the error of a failed migration, and where it rose."""
    run = _run(1, 0, settings)
    finish_steps.finish_export.side_effect = RuntimeError("Export failed for drive")

    with pytest.raises(RuntimeError):
        tasks.finish_migration(*_ids(run, user))

    task_result.refresh_from_db()
    assert task_result.status == states.FAILURE
    assert task_result.date_done is not None
    assert json.loads(task_result.result) == {
        "exc_type": "RuntimeError",
        "exc_message": ["Export failed for drive"],
        "exc_module": "builtins",
    }
    assert "RuntimeError: Export failed for drive" in task_result.traceback
