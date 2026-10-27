"""Working rows of a migration run, see MigrationRun and MigrationFile."""

import itertools
import os

from django.db import transaction

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


def delete_migration_run(task_id: str) -> None:
    """Purge the working rows of a run, its files included."""
    MigrationRun.objects.filter(task_id=task_id).delete()
