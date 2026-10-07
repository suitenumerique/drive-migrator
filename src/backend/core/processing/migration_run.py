"""Working rows of a migration run, see MigrationRun and MigrationFile."""

import itertools
import os

from core.models import MigrationFile, MigrationRun, Workspace
from core.processing.plan import MigrationPlan

BULK_BATCH_SIZE = 1000


def create_migration_run(
    workspace: Workspace, task_id: str, plan: MigrationPlan
) -> MigrationRun:
    """Store the plan of a run: its folders, one pending row per file, and one
    failed row per refused file."""
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


def record_retrieval(run: MigrationRun, creator, local_folder_path: str) -> None:
    """Store what FolderCreator did with each file of the run."""
    failed_by_id = {failed["id"]: failed for failed in creator.failed_files}
    files = list(run.files.all())

    for migration_file in files:
        source_id = migration_file.source["id"]
        if source_id in creator.written_files:
            migration_file.status = MigrationFile.Status.DOWNLOADED
            migration_file.size = os.path.getsize(
                os.path.join(local_folder_path, migration_file.path)
            )
        elif source_id in failed_by_id:
            migration_file.status = (
                MigrationFile.Status.NOT_WRITTEN
                if source_id in creator.not_written_ids
                else MigrationFile.Status.DOWNLOAD_FAILED
            )
            migration_file.error = failed_by_id[source_id]["error"]

    MigrationFile.objects.bulk_update(
        files, ["status", "size", "error"], batch_size=BULK_BATCH_SIZE
    )


def delete_migration_run(task_id: str) -> None:
    """Purge the working rows of a run, its files included."""
    MigrationRun.objects.filter(task_id=task_id).delete()
