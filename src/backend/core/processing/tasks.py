import os
from typing import Callable

from django.conf import settings
from django.utils import timezone

from celery import states
from celery.signals import before_task_publish, task_failure, task_success
from celery.utils.log import get_task_logger
from django_celery_results.models import TaskResult

from core.analytics import (
    posthog_capture,
    save_run_files_stats,
    workspaces_counts,
)
from core.backends.destination import (
    AbstractDestinationBackend,
    DestinationRegistry,
    FileExportStatus,
)
from core.backends.source import (
    AbstractSourceBackend,
    SourceFolder,
    SourceManager,
    SourceTokenExpired,
    truncate_folder_files,
)
from core.mails_manager import MailsManager
from core.models import ExtraTaskInfo, MigrationFile, MigrationRun, User, Workspace
from core.processing.file_exports import FileExports
from core.processing.folder_creator import FolderCreator
from core.processing.integrity import IntegrityTracker, Stage
from core.processing.migration_run import create_migration_run, delete_migration_run
from core.processing.plan import build_plan
from core.processing.rejected_files import (
    RejectedFilesArchive,
    drop_unlinked_kept_files,
    keep_refused_file,
)
from core.processing.token_status import log_token_status
from core.utils import get_dir_size, sizeof_fmt

from main.celery_app import app

logger = get_task_logger(__name__)


def list_work_dir():
    logger.info("Listing work dir")
    os.makedirs(settings.APP_WORK_DIR, exist_ok=True)
    with os.scandir(settings.APP_WORK_DIR) as it:
        for entry in it:
            size = 0
            if entry.is_dir():
                size = get_dir_size(entry.path)
            elif entry.is_file():
                size = entry.stat().st_size
            size_fmt = sizeof_fmt(size)
            logger.info("%s %s (%s)", entry.name, size_fmt, size)


def list_workspace_dir(workspace: Workspace):
    logger.info("Listing workspace dir")
    creator = FolderCreator()
    path = creator.get_workspace_path(workspace)
    files = []
    for root, _, filenames in os.walk(path):
        for filename in filenames:
            files.append(os.path.join(root, filename))

    logger.info("Listing %s files", len(files))
    for file in files:
        size = os.stat(file).st_size
        size_formatted = sizeof_fmt(size)
        logger.info("File: %s %s (%s) ...", file, size_formatted, size)


def cleanup_workspace_dir(workspace: Workspace):
    logger.info("Cleaning up %s directory ...", workspace.id)
    creator = FolderCreator()
    creator.delete_folder(workspace)
    logger.info("Cleaned up %s directory !", workspace.id)
    list_work_dir()


def debug_folder(folder: SourceFolder):
    logger.info("Debugging folder")

    def aux(f: SourceFolder, depth=0):
        logger.info(" " * depth + f.name)
        for child in f.children:
            aux(child, depth + 1)

    aux(folder)


def capture_migration_finished(extra_task: ExtraTaskInfo, status: str):
    workspace = extra_task.workspace
    integrity_summary = extra_task.integrity_report.get("summary", {})
    posthog_capture(
        "migration_finished",
        extra_task.user,
        {
            "status": status,
            "is_truncated": workspace.is_truncated,
            "download_errors_count": len(workspace.download_errors),
            "upload_errors_count": len(workspace.upload_errors),
            "integrity_check_passed": extra_task.integrity_check_passed,
            "migrated_files_count": integrity_summary.get("migrated_files_count"),
            "source_files_count": integrity_summary.get("source_files_count"),
            "analysis_unfinished_count": (
                integrity_summary.get("stages", {}).get(Stage.ANALYSIS_UNFINISHED, 0)
                if integrity_summary
                else None
            ),
            "duration_seconds": (
                timezone.now() - extra_task.task_result.date_created
            ).total_seconds(),
            "$set": workspaces_counts(extra_task.user),
            **extra_task.files_stats,
        },
        workspace=workspace,
    )


@app.task(bind=True)
def export(self, data):  # pylint: disable=unused-argument
    workspace_id = data["workspace"]["id"]
    logger.info("Starting workspace %s ...", workspace_id)

    workspace = Workspace.objects.get(id=workspace_id)
    logger.info(
        "Workspace title: %s, destination_statuses: %s",
        workspace.title,
        workspace.destination_statuses,
    )
    user = User.objects.get(id=data["user"]["id"])
    log_token_status(
        logger,
        f"Before migrating workspace {workspace_id}",
        user,
        resana=workspace.source_type == "resana",
        drive=workspace.get_destination_status("drive") == Workspace.Status.PENDING,
    )

    workspace.reset_migration_errors()
    RejectedFilesArchive.clear(workspace)

    source_backend = SourceManager().get_backend()
    list_work_dir()

    try:
        logger.info("Calling begin_export ...")
        source_backend.begin_export(workspace)

        logger.info("Calling get_workspace_structure ...")
        folder = source_backend.get_workspace_structure(workspace)
        debug_folder(folder)
        # Snapshots the source tree, so it must be created before truncation.
        integrity_tracker = IntegrityTracker(workspace, folder)

        file_limit = settings.MIGRATION_FILE_LIMIT_PER_WORKSPACE
        if file_limit > 0:
            workspace.is_truncated = truncate_folder_files(folder, file_limit)
            workspace.save()
            logger.info(
                "File limit %s applied, is_truncated=%s",
                file_limit,
                workspace.is_truncated,
            )
        integrity_tracker.set_kept_files(folder)

        run_failed = True
        run = None
        try:
            plan = build_plan(folder)
            run = create_migration_run(workspace, self.request.id, plan)
            _export_workspace(run, user, source_backend, integrity_tracker)
            run_failed = False
        finally:
            # Before the task_success/task_failure handlers delete the local folder.
            integrity_tracker.save(self.request.id, run_failed=run_failed)
            if run is not None:
                # Read from the run's rows, for the task handlers.
                save_run_files_stats(self.request.id, run)
                drop_unlinked_kept_files(run)
            delete_migration_run(self.request.id)
    finally:
        logger.info("Calling finalize_export ...")
        source_backend.finalize_export(workspace)

    logger.info("Task done")

    return True


def _export_workspace(
    run: MigrationRun,
    user: User,
    source_backend: AbstractSourceBackend,
    integrity_tracker: IntegrityTracker,
) -> None:
    workspace = run.workspace
    creator = FolderCreator()
    local_path = creator.get_workspace_path(workspace)
    integrity_tracker.set_retrieval(run)

    # Before the downloads: file-by-file destinations need the members to close.
    logger.info("Calling prepare_export ...")
    source_backend.prepare_export(workspace, local_path)

    destinations = _pending_destinations(workspace)
    for destination in destinations:
        # Registered first so a destination that fails midway is still checked.
        integrity_tracker.add_exported_destination(destination)
    file_by_file = [d for d in destinations if d.exports_file_by_file]
    whole_folder = [d for d in destinations if not d.exports_file_by_file]
    file_exports = FileExports(run, user, file_by_file)

    try:
        file_exports.open()
        logger.info("Calling create_folder ...")
        try:
            local_path = creator.create_folder(
                workspace,
                run,
                source_backend,
                after_download=_file_handover(file_exports, file_by_file, whole_folder),
            )
        except SourceTokenExpired:
            # Also keep the files that failed before the token expired.
            _save_download_errors(workspace, creator)
            raise
        _check_downloads(workspace, creator)
        list_workspace_dir(workspace)
    except BaseException:
        file_exports.abort()
        raise

    failures = file_exports.close()
    for destination in whole_folder:
        logger.info("Calling %s export ...", destination.name)
        destination.export(workspace, user, local_path)

    if failures:
        raise RuntimeError(
            f"Export failed for {', '.join(failures)} on workspace {workspace.id}"
        ) from next(iter(failures.values()))


def _file_handover(
    file_exports: FileExports,
    file_by_file: list[AbstractDestinationBackend],
    whole_folder: list[AbstractDestinationBackend],
) -> Callable[[MigrationFile, str], None] | None:
    """What to do with each downloaded file: hand it to the file-by-file
    destinations, and drop it unless a legacy destination reads it back. A file
    a destination did not take is kept on S3 first, for the user."""
    if not file_by_file:
        return None
    if whole_folder:
        return file_exports.put

    def put_and_drop(migration_file: MigrationFile, local_path: str) -> None:
        file_exports.put(migration_file, local_path)
        if not all(
            result.get("status") == FileExportStatus.SENT
            for result in migration_file.results.values()
        ):
            keep_refused_file(file_exports.run, migration_file, local_path)
        os.remove(local_path)

    return put_and_drop


def _pending_destinations(workspace: Workspace) -> list[AbstractDestinationBackend]:
    destinations = []
    for destination in DestinationRegistry.get_all():
        status = workspace.get_destination_status(destination.name)
        logger.info("%s status = %s", destination.name, status)
        if status == Workspace.Status.PENDING:
            destinations.append(destination)
    return destinations


def _save_download_errors(workspace: Workspace, creator: FolderCreator) -> None:
    if creator.failed_files:
        workspace.download_errors = creator.failed_files
        workspace.save(update_fields=["download_errors"])
        logger.warning(
            "%s file(s) failed to download: %s",
            len(creator.failed_files),
            creator.failed_files,
        )


def _check_downloads(workspace: Workspace, creator: FolderCreator) -> None:
    """Keep the failed downloads on the workspace; fail when none succeeded."""
    _save_download_errors(workspace, creator)

    if creator.files_count and creator.files_success == 0:
        raise RuntimeError(
            f"All {creator.files_count} file(s) failed to download for "
            f"workspace {workspace.id}"
        )


# From https://github.com/celery/django-celery-results/issues/286#issuecomment-1279161047
@before_task_publish.connect
def create_task_result_on_publish(sender=None, headers=None, body=None, **kwargs):  # pylint: disable=unused-argument
    if "task" not in headers:
        return

    TaskResult.objects.store_result(
        "application/json",
        "utf-8",
        headers["id"],
        None,
        states.PENDING,
        task_name=headers["task"],
        task_args=headers["argsrepr"],
        task_kwargs=headers["kwargsrepr"],
    )


@task_success.connect
def task_success(sender=None, **kwargs):  # pylint: disable=unused-argument
    task_result = TaskResult.objects.filter(task_id=sender.request.id).first()
    if task_result is None:
        return
    extra_task = ExtraTaskInfo.objects.filter(task_result=task_result).first()
    if extra_task is None:
        return
    workspace = extra_task.workspace
    workspace.save()
    capture_migration_finished(extra_task, "success")

    cleanup_workspace_dir(workspace)


@task_failure.connect
def task_failure(sender=None, **kwargs):
    task_result = TaskResult.objects.filter(task_id=sender.request.id).first()
    if task_result is None:
        return
    extra_task = ExtraTaskInfo.objects.filter(task_result=task_result).first()
    if extra_task is None:
        return
    workspace = extra_task.workspace

    for dest_name, status in workspace.destination_statuses.items():
        if status == Workspace.Status.PENDING:
            workspace.set_destination_status(dest_name, Workspace.Status.FAILURE)
    workspace.save()
    capture_migration_finished(extra_task, "failure")

    cleanup_workspace_dir(workspace)

    mail_manager = MailsManager()
    mail_manager.send_fail_mail(
        extra_task.user,
        workspace,
        source_token_expired=isinstance(kwargs.get("exception"), SourceTokenExpired),
    )
