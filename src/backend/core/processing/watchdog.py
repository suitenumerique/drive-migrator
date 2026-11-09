"""Get the migration runs that stopped making progress moving again, or fail
them, so that none is left unfinished with its source workspace locked."""

import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from core.backends.source import SourceManager, SourceTokenExpired
from core.models import MigrationRun
from core.processing.tasks import (
    RunStalled,
    dispatch_batch,
    finish_run,
    publish_file,
    publish_finish,
    publish_finish_again,
)

logger = logging.getLogger(__name__)


def watch_runs() -> None:
    """Handle every run without news for MIGRATION_RUN_STALL_SECONDS."""
    stalled_since = timezone.now() - timedelta(
        seconds=settings.MIGRATION_RUN_STALL_SECONDS
    )
    runs = (
        MigrationRun.objects.select_related("workspace__migration_user")
        # Read by the finish only, which loads them when it runs here.
        .defer("integrity_state", "dirs")
        .filter(updated_at__lt=stalled_since)
    )
    for run in runs:
        try:
            watch_run(run)
        # Broad on purpose: one run must not keep the next ones unwatched.
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Could not handle the stalled run %s", run.pk)


def watch_run(run: MigrationRun) -> None:
    """Get a stalled run moving again, or fail it, unless it made progress
    since it was read."""
    user = run.workspace.migration_user
    if run.finish_published:
        _watch_finish(run, user)
        return

    if not run.remaining_files:
        # Every file is done: only the queuing of the finish was lost.
        if _touch(run):
            logger.warning("Run %s is done but its finish was never queued", run.pk)
            publish_finish(run, user)
        return

    if run.redispatches < settings.MIGRATION_RUN_MAX_REDISPATCHES and _relaunch(
        run, user
    ):
        return

    if _touch(run, stalled=True):
        logger.warning("Run %s stalled: failing it", run.pk)
        publish_finish(run, user)


def _watch_finish(run: MigrationRun, user) -> None:
    """A finish never started is queued again, as files are. One that started
    gets the patience of the files, the stall time once per redispatch, plus
    once. A lost finish of a done run is queued again, to close its
    destinations in a worker, up to twice the redispatches; otherwise, or then,
    it is done here, as failed."""
    if run.finish_started:
        patience = settings.MIGRATION_RUN_STALL_SECONDS * (
            settings.MIGRATION_RUN_MAX_REDISPATCHES + 1
        )
        if run.updated_at >= timezone.now() - timedelta(seconds=patience):
            return
    # Every file done: the migration succeeded, only its finish was lost.
    done = not run.remaining_files and not run.stopped
    redispatches = settings.MIGRATION_RUN_MAX_REDISPATCHES * (2 if done else 1)
    if (done or not run.finish_started) and run.redispatches < redispatches:
        # claim_finish() lets the finish queued take it over.
        if _touch(run, finish_started=False, redispatches=F("redispatches") + 1):
            logger.warning("The finish of run %s was lost: queuing it", run.pk)
            publish_finish_again(run, user)
        return
    _fail(run, user)


def _relaunch(run: MigrationRun, user) -> bool:
    """Queue again the files dispatched and never finished, or the next batch
    when it was never queued. False when there is nothing to queue again."""
    unfinished = list(
        run.files.filter(position__lt=run.cursor, finished_at=None)
        .order_by("position")
        .values_list("position", flat=True)
    )
    # No file dispatched yet means the start was lost; a stopped run queues no
    # batch. Either way the run is failed.
    if not unfinished and (not run.cursor or run.stopped):
        return False
    if _touch(run, redispatches=F("redispatches") + 1):
        if unfinished:
            logger.warning(
                "Run %s stalled: queuing its %s unfinished file(s) again",
                run.pk,
                len(unfinished),
            )
            for position in unfinished:
                publish_file(run, user, position)
        else:
            logger.warning("Run %s stalled: queuing its next batch again", run.pk)
            dispatch_batch(run, user)
    return True


def _touch(run: MigrationRun, **fields) -> bool:
    """Update the run unless it made progress since it was read."""
    return bool(
        MigrationRun.objects.filter(pk=run.pk, updated_at=run.updated_at).update(
            updated_at=timezone.now(), **fields
        )
    )


def _fail(run: MigrationRun, user) -> None:
    """Do the finish of the run here, as failed: its own task never ended."""
    # A finish task starting at last finds the finish taken. One that started
    # and went silent this long is taken over.
    if not _touch(run, stalled=True, finish_started=True):
        return
    logger.warning("The finish of run %s never ended: failing it", run.pk)
    run.stalled = True
    source_backend = SourceManager().get_backend()
    source_backend.resume_export(run.workspace)
    try:
        finish_run(run, user, source_backend)
    # The error of the run, reported to the user already.
    except (RunStalled, SourceTokenExpired):
        logger.info("Run %s failed and reported", run.pk)
