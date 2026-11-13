"""Tests for the watchdog getting stalled migration runs moving again, or
failing them."""

from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.utils import timezone

import pytest

from core.backends.source import SourceFile, SourceFolder, SourceTokenExpired
from core.factories import UserFactory, WorkspaceFactory
from core.models import MigrationRun
from core.processing import tasks, watchdog
from core.processing.migration_run import create_migration_run, finish_file
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


@pytest.fixture(name="watch")
def fixture_watch(django_capture_on_commit_callbacks):
    """Watch the runs, publishing once the transaction commits."""

    def watch():
        with django_capture_on_commit_callbacks(execute=True):
            watchdog.watch_runs()

    return watch


@pytest.fixture(name="limits", autouse=True)
def fixture_limits(settings):
    settings.MIGRATION_BATCH_MAX_FILES = 2
    settings.MIGRATION_BATCH_MAX_BYTES = 0
    settings.MIGRATION_RUN_STALL_SECONDS = 3600
    settings.MIGRATION_RUN_MAX_REDISPATCHES = 2


def _dispatched_run(files_count=3, task_id="task-1", dispatched=True):
    """A run whose first batch of 2 files was dispatched, unless its start was
    lost."""
    files = [
        SourceFile(id=f"f{i}", name=f"doc{i}", extension=".txt", download_url="x")
        for i in range(files_count)
    ]
    plan = build_plan(SourceFolder(name="root", files=files))
    workspace = WorkspaceFactory(migration_user=UserFactory())
    run = create_migration_run(workspace, task_id, plan)
    if dispatched:
        tasks.dispatch_batch(run, workspace.migration_user)
    return run


def _idle(run, hours=2, **fields):
    """Leave the run without news for hours."""
    MigrationRun.objects.filter(pk=run.pk).update(
        updated_at=timezone.now() - timedelta(hours=hours), **fields
    )


def _ids(run):
    return str(run.pk), str(run.workspace.migration_user.pk)


def test_a_run_making_progress_is_left_alone(published, watch):
    """A run with recent news is not touched."""
    run = _dispatched_run()

    watch()

    assert not published
    run.refresh_from_db()
    assert (run.redispatches, run.stalled) == (0, False)


def test_the_unfinished_files_of_a_stalled_run_are_dispatched_again(published, watch):
    """The files dispatched and never finished, e.g. lost with a dead worker,
    are queued again; the run counts it and shows alive."""
    run = _dispatched_run()
    finish_file(run, 0)
    _idle(run)

    watch()

    assert published == [("export_file", *_ids(run), 1)]
    run.refresh_from_db()
    assert run.redispatches == 1
    assert run.updated_at > timezone.now() - timedelta(minutes=1)
    assert not run.stalled


def test_a_run_still_stalled_after_its_redispatches_is_failed(published, watch):
    """Once its redispatches are spent, the run is marked stalled and its finish
    queued, to abort its destinations and tell the user."""
    run = _dispatched_run()
    _idle(run, redispatches=2)

    watch()

    assert published == [("finish_migration", *_ids(run))]
    run.refresh_from_db()
    assert run.stalled
    assert run.finish_published


def test_a_file_done_gives_the_run_its_redispatches_back():
    """The redispatches count the relaunches without progress: a migration of
    several days survives one stall per redeploy."""
    run = _dispatched_run()
    MigrationRun.objects.filter(pk=run.pk).update(redispatches=2)

    finish_file(run, 0)

    run.refresh_from_db()
    assert run.redispatches == 0


def test_a_run_whose_start_was_lost_is_failed(published, watch):
    """A run planned by a task that died before queuing any file is failed:
    its destinations may not even be open."""
    run = _dispatched_run(dispatched=False)
    _idle(run)

    watch()

    assert published == [("finish_migration", *_ids(run))]
    run.refresh_from_db()
    assert run.stalled


def test_a_run_whose_next_batch_was_never_queued_gets_it(published, watch):
    """The last file of a batch died before queuing the next one: the next
    batch is queued, counted as a redispatch."""
    run = _dispatched_run()
    finish_file(run, 0)
    finish_file(run, 1)
    _idle(run)

    watch()

    assert published == [("export_file", *_ids(run), 2)]
    run.refresh_from_db()
    assert (run.redispatches, run.stalled) == (1, False)


def test_a_done_run_whose_finish_was_never_queued_gets_it(published, watch):
    """Every file is done but the finish was never queued: it is, and the run
    is not failed."""
    run = _dispatched_run(files_count=2)
    finish_file(run, 0)
    finish_file(run, 1)
    _idle(run)

    watch()

    assert published == [("finish_migration", *_ids(run))]
    run.refresh_from_db()
    assert run.finish_published
    assert not run.stalled


def test_a_stalled_run_whose_finish_was_lost_is_failed_by_the_watchdog(
    published, watch
):
    """The finish of the run was queued and never ended: the watchdog fails the
    run itself, the source resumed first."""
    run = _dispatched_run()
    _idle(run, hours=4, finish_published=True, finish_started=True)

    with (
        patch("core.processing.watchdog.SourceManager") as manager_cls,
        patch("core.processing.watchdog.finish_run") as finish_run,
    ):
        finish_run.side_effect = tasks.RunStalled("stalled")
        watch()

    source_backend = manager_cls.return_value.get_backend.return_value
    source_backend.resume_export.assert_called_once_with(run.workspace)
    failed_run, user, backend = finish_run.call_args.args
    assert (failed_run.pk, failed_run.stalled) == (run.pk, True)
    run.refresh_from_db()
    # The finish task, if it starts at last, leaves the run to the watchdog.
    assert run.finish_started
    assert user == run.workspace.migration_user
    assert backend is source_backend
    assert not published


def test_a_finish_that_started_meanwhile_is_not_failed(published):
    """The finish of the run started between the watchdog reading the run and
    failing it: the run is left to it."""
    run = _dispatched_run()
    _idle(run, hours=4, finish_published=True, finish_started=True)
    read_by_the_watchdog = MigrationRun.objects.select_related(
        "workspace__migration_user"
    ).get(pk=run.pk)
    MigrationRun.objects.filter(pk=run.pk).update(updated_at=timezone.now())

    with (
        patch("core.processing.watchdog.SourceManager"),
        patch("core.processing.watchdog.finish_run") as finish_run,
    ):
        watchdog.watch_run(read_by_the_watchdog)

    finish_run.assert_not_called()
    run.refresh_from_db()
    assert not run.stalled
    assert not published


def test_a_finish_lost_before_it_started_is_queued_again(published, watch):
    """The finish never started, e.g. lost with the worker that reserved it:
    it is queued again, counted as a redispatch, and the run is not failed."""
    run = _dispatched_run()
    _idle(run, finish_published=True)

    with patch("core.processing.watchdog.finish_run") as finish_run:
        watch()

    finish_run.assert_not_called()
    assert published == [("finish_migration", *_ids(run))]
    run.refresh_from_db()
    assert (run.redispatches, run.stalled) == (1, False)


def test_a_finish_never_started_after_its_redispatches_is_failed(published, watch):
    """Once its redispatches are spent, a finish that never started is done
    by the watchdog, as failed."""
    run = _dispatched_run()
    _idle(run, finish_published=True, redispatches=2)

    with (
        patch("core.processing.watchdog.SourceManager"),
        patch("core.processing.watchdog.finish_run") as finish_run,
    ):
        watch()

    finish_run.assert_called_once()
    assert not published


def test_a_published_finish_gets_the_patience_of_the_files(published, watch):
    """A finish queued behind the batches of other runs is waited for as long
    as files are: the stall time once per redispatch, plus once."""
    run = _dispatched_run()
    _idle(run, hours=2, finish_published=True, finish_started=True)

    with patch("core.processing.watchdog.finish_run") as finish_run:
        watch()

    finish_run.assert_not_called()
    run.refresh_from_db()
    assert not run.stalled
    assert not published


def test_a_stalled_run_whose_token_also_expired_is_failed_without_error(
    published, watch
):
    """The finish done by the watchdog raises the expired token first: the
    run was handled all the same, so nothing is logged as an error."""
    run = _dispatched_run()
    _idle(run, hours=4, finish_published=True, finish_started=True)

    with (
        patch("core.processing.watchdog.SourceManager"),
        patch("core.processing.watchdog.finish_run") as finish_run,
        patch("core.processing.watchdog.logger") as logger,
    ):
        finish_run.side_effect = SourceTokenExpired("token expired")
        watch()

    logger.exception.assert_not_called()
    assert not published


def _done_run():
    """A run whose every file is done."""
    run = _dispatched_run(files_count=2)
    finish_file(run, 0)
    finish_file(run, 1)
    return run


@pytest.mark.parametrize(
    "lost",
    [
        {"finish_started": True, "hours": 4},
        {"finish_started": False, "redispatches": 2},
    ],
    ids=["silent", "never_started"],
)
def test_the_lost_finish_of_a_done_run_is_queued_again(published, watch, lost):
    """Every file is done: the migration succeeded, so its finish is queued
    again, to close the destinations in a worker, rather than failed."""
    run = _done_run()
    _idle(run, finish_published=True, **lost)

    with patch("core.processing.watchdog.finish_run") as finish_run:
        watch()

    finish_run.assert_not_called()
    assert published == [("finish_migration", *_ids(run))]
    run.refresh_from_db()
    # Claimed again by the finish queued.
    assert not run.finish_started
    assert not run.stalled
    assert run.redispatches == lost.get("redispatches", 0) + 1


@pytest.mark.parametrize("finish_started", [False, True])
def test_the_finish_of_a_done_run_lost_again_and_again_is_failed(
    published, watch, finish_started
):
    """A finish lost each time it is queued, e.g. killing its worker, is
    queued twice the redispatches, then done here, as failed: the source
    workspace is not left locked."""
    run = _done_run()
    _idle(
        run,
        hours=4,
        finish_published=True,
        finish_started=finish_started,
        redispatches=4,
    )

    with (
        patch("core.processing.watchdog.SourceManager"),
        patch("core.processing.watchdog.finish_run") as finish_run,
    ):
        finish_run.side_effect = tasks.RunStalled("stalled")
        watch()

    assert finish_run.call_args.args[0].stalled
    assert not published


def test_the_lost_finish_of_a_done_run_whose_token_expired_fails_it(watch):
    """A run stopped by an expired source token is failed all the same, even
    with every file done: its destinations were never told to close."""
    run = _done_run()
    _idle(run, hours=4, finish_published=True, finish_started=True)
    MigrationRun.objects.filter(pk=run.pk).update(source_token_expired=True)

    with (
        patch("core.processing.watchdog.SourceManager"),
        patch("core.processing.watchdog.finish_run") as finish_run,
    ):
        finish_run.side_effect = SourceTokenExpired("token expired")
        watch()

    run.refresh_from_db()
    assert run.stalled


def test_a_run_failing_to_be_watched_does_not_stop_the_others(published, watch):
    """One run the watchdog cannot handle leaves the next ones watched."""
    first = _dispatched_run(task_id="task-1")
    second = _dispatched_run(task_id="task-2")
    _idle(first, hours=4, finish_published=True, finish_started=True)
    _idle(second, redispatches=2)

    with patch(
        "core.processing.watchdog.SourceManager", side_effect=RuntimeError("boom")
    ):
        watch()

    assert published == [("finish_migration", *_ids(second))]


def test_the_runs_are_read_without_their_big_states(watch):
    """Every pass reads the runs without news: not the states only their
    finish needs."""
    _idle(_dispatched_run())

    with patch("core.processing.watchdog.watch_run") as watch_run:
        watch()

    watched = watch_run.call_args.args[0]
    assert {"integrity_state", "dirs"} <= watched.get_deferred_fields()


def test_the_command_watches_the_runs():
    """watch_migration_runs is what the cron job calls."""
    with patch("core.management.commands.watch_migration_runs.watch_runs") as watch:
        call_command("watch_migration_runs")

    watch.assert_called_once_with()
