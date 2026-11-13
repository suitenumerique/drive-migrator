"""Working rows of a migration run, see MigrationRun and MigrationFile."""

import itertools
import os

from django.conf import settings
from django.db import transaction
from django.utils import timezone

import magic
from celery.utils.log import get_task_logger

from core.backends.source import (
    AbstractSourceBackend,
    SourceFile,
    SourceTokenExpired,
)
from core.models import MigrationFile, MigrationRun, Workspace
from core.processing.plan import MigrationPlan

logger = get_task_logger(__name__)

BULK_BATCH_SIZE = 1000

# What the counting of a run reads, under its lock: not its big JSON states.
_COUNTERS = (
    "cursor",
    "in_flight",
    "remaining_files",
    "redispatches",
    "source_token_expired",
    "stalled",
    "updated_at",
)


@transaction.atomic
def create_migration_run(
    workspace: Workspace, task_id: str, plan: MigrationPlan
) -> MigrationRun:
    """Store the plan of a run: its folders, one pending row per file, and one
    failed row per refused file. Rows left by a killed run of the same task are
    replaced, since a retry starts over from a new plan."""
    delete_migration_run(task_id)
    run = MigrationRun.objects.create(
        workspace=workspace,
        task_id=task_id,
        dirs=plan.dirs,
        remaining_files=len(plan.files),
    )
    planned_rows = (
        MigrationFile(
            run=run,
            position=position,
            source=planned.source.to_dict(),
            path=planned.path,
            expected_size=planned.source.size,
        )
        for position, planned in enumerate(plan.files)
    )
    refused_rows = (
        MigrationFile(
            run=run,
            position=position,
            source=refused.source.to_dict(),
            path=refused.path,
            status=MigrationFile.Status.DOWNLOAD_FAILED,
            error=refused.error,
        )
        for position, refused in enumerate(plan.refused, start=len(plan.files))
    )
    MigrationFile.objects.bulk_create(
        itertools.chain(planned_rows, refused_rows), batch_size=BULK_BATCH_SIZE
    )
    return run


def take_next_batch(run: MigrationRun) -> list[int] | None:
    """Take the next pending files of the run from its cursor, within the batch
    limits, and count them in flight. Return their positions, or None while a
    batch is still in flight.

    A batch holds at least one file, even one bigger than the bytes limit; a
    size the source did not announce counts as 0.
    """
    max_files = settings.MIGRATION_BATCH_MAX_FILES
    max_bytes = settings.MIGRATION_BATCH_MAX_BYTES
    with transaction.atomic():
        locked = (
            MigrationRun.objects.select_for_update().only(*_COUNTERS).get(pk=run.pk)
        )
        # One batch at a time, even when two tasks ask for the next one.
        if locked.in_flight:
            return None
        rows = (
            locked.files.filter(
                position__gte=locked.cursor, status=MigrationFile.Status.PENDING
            )
            .order_by("position")
            .values_list("position", "expected_size")
        )
        if max_files:
            rows = rows[:max_files]
        positions = []
        total_bytes = 0
        for position, expected_size in rows.iterator():
            total_bytes += expected_size or 0
            if positions and max_bytes and total_bytes > max_bytes:
                break
            positions.append(position)
        if positions:
            _update(
                locked,
                cursor=positions[-1] + 1,
                in_flight=locked.in_flight + len(positions),
            )
    return positions


def _update(locked: MigrationRun, **fields) -> None:
    """Save counters of a run read with only them: save() would validate the
    whole row, reading back each deferred field."""
    fields["updated_at"] = timezone.now()
    for name, value in fields.items():
        setattr(locked, name, value)
    MigrationRun.objects.filter(pk=locked.pk).update(**fields)


def finish_file(run: MigrationRun, position: int) -> MigrationRun | None:
    """Count a dispatched file of the run done; return the run as it stands
    then, so that a single file task sees its batch, or the run, done. None
    when the file was already counted, its task delivered twice, or when the
    run was purged meanwhile."""
    with transaction.atomic():
        locked = (
            MigrationRun.objects.select_for_update()
            .only(*_COUNTERS)
            .filter(pk=run.pk)
            .first()
        )
        if locked is None or not locked.files.filter(
            position=position, finished_at=None
        ).update(finished_at=timezone.now()):
            return None
        # The run made progress: the watchdog counts relaunches without any.
        _update(
            locked,
            in_flight=locked.in_flight - 1,
            remaining_files=locked.remaining_files - 1,
            redispatches=0,
        )
    return locked


def claim_finish(run: MigrationRun) -> bool:
    """Claim the finish of the run, showing it alive; False when a finish of
    it already started."""
    return bool(
        MigrationRun.objects.filter(pk=run.pk, finish_started=False).update(
            finish_started=True, updated_at=timezone.now()
        )
    )


def mark_run_alive(run: MigrationRun) -> None:
    """Show the run is making progress, for the watchdog."""
    MigrationRun.objects.filter(pk=run.pk).update(updated_at=timezone.now())


def stop_run(run: MigrationRun) -> None:
    """Stop the run on an expired source token: its files left are skipped."""
    run.source_token_expired = True
    MigrationRun.objects.filter(pk=run.pk).update(source_token_expired=True)


def record_file_error(migration_file: MigrationFile, error: Exception) -> None:
    """Keep an unexpected error on the row of its file; a file not downloaded
    yet counts as failed to download."""
    fields = {"error": str(error)}
    if migration_file.status == MigrationFile.Status.PENDING:
        fields["status"] = MigrationFile.Status.DOWNLOAD_FAILED
    for name, value in fields.items():
        setattr(migration_file, name, value)
    MigrationFile.objects.filter(pk=migration_file.pk).update(**fields)


def download_migration_file(
    migration_file: MigrationFile,
    source_backend: AbstractSourceBackend,
    destination: str,
) -> None:
    """Download one file of a run to destination, then record the outcome on its
    row. One file failing must not abort the rest of the run, so only raises
    SourceTokenExpired, once recorded: every remaining file would fail the same
    way."""
    file = SourceFile.from_dict(migration_file.source)
    size = None
    mime_type = ""
    error = ""
    token_expired = None
    try:
        source_backend.download_file(file, destination)
    # Broad on purpose: source_backend is backend-agnostic (Resana, Osmose,
    # filesystem...), so we can't target a specific exception type here.
    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        logger.error("Failed to download %s: %s", file.name_with_extension, exc)
        if os.path.exists(destination):
            os.remove(destination)
        status = MigrationFile.Status.DOWNLOAD_FAILED
        error = str(exc)
        if isinstance(exc, SourceTokenExpired):
            token_expired = exc
    else:
        if os.path.isfile(destination):
            status = MigrationFile.Status.DOWNLOADED
            size = os.path.getsize(destination)
            mime_type = magic.from_file(destination, mime=True)
        else:
            logger.error(
                "Download of %s reported success but wrote no file",
                file.name_with_extension,
            )
            status = MigrationFile.Status.NOT_WRITTEN
            error = "Download reported success but wrote no file"

    migration_file.status = status
    migration_file.size = size
    migration_file.mime_type = mime_type
    migration_file.error = error
    # A single UPDATE: save() would also validate the row with extra queries.
    MigrationFile.objects.filter(pk=migration_file.pk).update(
        status=status, size=size, mime_type=mime_type, error=error
    )
    if token_expired is not None:
        raise token_expired


def failed_downloads(run: MigrationRun) -> list[dict]:
    """The files of the run that did not reach the local disk, refused ones
    included, as Workspace.download_errors lists them."""
    return [
        {
            "id": source["id"],
            "name": source["name"] + source["extension"],
            "path": path,
            "error": error,
        }
        for source, path, error in run.files.filter(
            status__in=[
                MigrationFile.Status.DOWNLOAD_FAILED,
                MigrationFile.Status.NOT_WRITTEN,
            ]
        )
        .order_by("position")
        .values_list("source", "path", "error")
    ]


def run_exists(task_id: str) -> bool:
    """Whether the task already planned its run, e.g. delivered twice."""
    return MigrationRun.objects.filter(task_id=task_id).exists()


def delete_migration_run(task_id: str) -> None:
    """Purge the working rows of a run, its files included."""
    MigrationRun.objects.filter(task_id=task_id).delete()
