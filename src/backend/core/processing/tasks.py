import json
import os
import traceback
from typing import Callable

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from celery import states
from celery.exceptions import Ignore
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
from core.processing.migration_run import (
    create_migration_run,
    delete_migration_run,
    failed_downloads,
    finish_file,
    record_file_error,
    run_exists,
    stop_run,
    take_next_batch,
)
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
def export(self, data):
    if run_exists(self.request.id):
        # Delivered again by the broker: the run goes on with its own tasks.
        logger.warning("Run of task %s already started: skipping", self.request.id)
        raise Ignore()

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

    dispatched = False
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

        run = None
        try:
            plan = build_plan(folder)
            run = create_migration_run(workspace, self.request.id, plan)
            try:
                _start_export(run, user, source_backend, integrity_tracker)
                dispatch_batch(run, user)
            except BaseException:
                _abort_export(run, user)
                raise
        except BaseException:
            _clean_up_run(self.request.id, run, integrity_tracker, run_failed=True)
            raise
        dispatched = True
    finally:
        if not dispatched:
            logger.info("Calling finalize_export ...")
            source_backend.finalize_export(workspace)

    logger.info("Run %s started", run.pk)
    # The tasks of the run carry on, and finish_migration reports the outcome.
    raise Ignore()


def dispatch_batch(run, user) -> None:
    """Queue a task for each file of the next batch of the run, or the finish of
    the run when it has no file left to dispatch."""
    positions = take_next_batch(run)
    for position in positions:
        _publish(export_file, run, user, position)
    if not positions:
        _publish_finish(run, user)


def _publish(task, run, user, *args) -> None:
    # A worker must never read a run whose changes are not committed yet.
    transaction.on_commit(lambda: task.delay(str(run.pk), str(user.pk), *args))


def _publish_finish(run, user) -> None:
    # Conditional, so that a run is finished only once.
    if MigrationRun.objects.filter(pk=run.pk, finish_published=False).update(
        finish_published=True
    ):
        _publish(finish_migration, run, user)


def _load_run(run_id):
    return MigrationRun.objects.select_related("workspace").filter(pk=run_id).first()


@app.task(ignore_result=True)
def export_file(run_id, user_id, position):
    """Export one file of a run, then queue what comes next once its batch, or
    the run, is done."""
    run = _load_run(run_id)
    if run is None:
        logger.warning("Run %s is gone: skipping its file %s", run_id, position + 1)
        return
    user = User.objects.get(pk=user_id)
    if run.source_token_expired:
        logger.info("Run %s stopped: skipping its file %s", run.pk, position + 1)
    else:
        source_backend = SourceManager().get_backend()
        source_backend.resume_export(run.workspace)
        run_file(run, user, source_backend, run.files.get(position=position))

    run = finish_file(run, position)
    if run.in_flight:
        return
    if run.remaining_files and not run.source_token_expired:
        dispatch_batch(run, user)
    else:
        _publish_finish(run, user)


def run_file(run, user, source_backend, migration_file):
    """Export one file of the run. Whatever goes wrong, the run goes on with
    its next files, except on an expired source token, which stops it."""
    try:
        _export_file(run, user, source_backend, migration_file)
    except SourceTokenExpired:
        logger.warning("The source token expired: stopping run %s", run.pk)
        stop_run(run)
    # Broad on purpose: one file failing must not block the rest of the run.
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.exception(
            "File %s of run %s failed", migration_file.position + 1, run.pk
        )
        record_file_error(migration_file, error)


@app.task(ignore_result=True)
def finish_migration(run_id, user_id):
    """Close the destinations of a run whose files are all done, purge the run,
    release the source, then report the outcome."""
    run = MigrationRun.objects.select_related("workspace").get(pk=run_id)
    user = User.objects.get(pk=user_id)
    source_backend = SourceManager().get_backend()
    source_backend.resume_export(run.workspace)
    finish_run(run, user, source_backend)


def finish_run(run, user, source_backend):
    """Raise the error of a failed run once it is reported."""
    integrity_tracker = IntegrityTracker.from_run(run)
    error = None
    try:
        run_failed = True
        try:
            if run.source_token_expired:
                _abort_export(run, user)
                raise SourceTokenExpired("The source token expired during the run.")
            _finish_export(run, user)
            run_failed = False
        # Broad on purpose: reported to the user first, then raised again.
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            error = exc
        finally:
            _clean_up_run(run.task_id, run, integrity_tracker, run_failed)
    finally:
        logger.info("Calling finalize_export ...")
        source_backend.finalize_export(run.workspace)

    _record_outcome(run.task_id, error)
    extra_task = _extra_task_of(run.task_id)
    if extra_task is not None:
        if error is None:
            on_export_success(extra_task)
        else:
            on_export_failure(extra_task, error)
    if error is not None:
        raise error
    logger.info("Run %s done", run.pk)


def _start_export(run, user, source_backend, integrity_tracker):
    """Open the run: fetch the members, open the destinations and prepare the
    local folder."""
    workspace = run.workspace
    creator = FolderCreator()
    integrity_tracker.set_retrieval(run)

    # Before the downloads: the destinations need the members to close.
    logger.info("Calling prepare_export ...")
    source_backend.prepare_export(workspace, creator.get_workspace_path(workspace))

    destinations = _pending_destinations(workspace)
    for destination in destinations:
        # Registered first so a destination that fails midway is still checked.
        integrity_tracker.add_exported_destination(destination)
    FileExports(run, user, destinations).open()
    creator.prepare_folder(workspace, run)


def _export_file(run, user, source_backend, migration_file):
    """Download one file of the run and hand it to the destinations."""
    workspace = run.workspace
    destinations = _pending_destinations(workspace)
    try:
        local_path = FolderCreator().download_file(
            workspace, migration_file, source_backend
        )
    except SourceTokenExpired:
        # The run stops: keep the files that failed before the token expired.
        _save_download_errors(workspace, run)
        raise
    handover = _file_handover(FileExports(run, user, destinations), destinations)
    if local_path is not None and handover is not None:
        handover(migration_file, local_path)


def _finish_export(run, user):
    """Close the destinations once every file of the run is done; raise when
    the run failed."""
    workspace = run.workspace
    file_exports = FileExports(run, user, _pending_destinations(workspace))
    try:
        _check_downloads(workspace, run)
        list_workspace_dir(workspace)
    except BaseException:
        file_exports.abort()
        raise

    failures = file_exports.close()
    if failures:
        raise RuntimeError(
            f"Export failed for {', '.join(failures)} on workspace {workspace.id}"
        ) from next(iter(failures.values()))


def _abort_export(run, user):
    """Leave no destination half open after a fatal run error."""
    FileExports(run, user, _pending_destinations(run.workspace)).abort()


def _clean_up_run(task_id, run, integrity_tracker, run_failed):
    """Save what the task handlers read, then purge the working rows of the
    run, whatever its outcome."""
    # Before the task_success/task_failure handlers delete the local folder.
    integrity_tracker.save(task_id, run_failed=run_failed)
    if run is not None:
        # Read from the run's rows, for the task handlers.
        save_run_files_stats(task_id, run)
        drop_unlinked_kept_files(run)
    delete_migration_run(task_id)


def _file_handover(
    file_exports: FileExports, destinations: list[AbstractDestinationBackend]
) -> Callable[[MigrationFile, str], None] | None:
    """What to do with each downloaded file: hand it to the destinations, then
    drop it. A file a destination did not take is kept on S3 first, for the
    user."""
    if not destinations:
        return None

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
    return [
        destination
        for destination in DestinationRegistry.get_all()
        if workspace.get_destination_status(destination.name)
        == Workspace.Status.PENDING
    ]


def _save_download_errors(workspace: Workspace, run: MigrationRun) -> list[dict]:
    failed = failed_downloads(run)
    if failed:
        workspace.download_errors = failed
        workspace.save(update_fields=["download_errors"])
        logger.warning("%s file(s) failed to download: %s", len(failed), failed)
    return failed


def _check_downloads(workspace: Workspace, run: MigrationRun) -> None:
    """Keep the failed downloads on the workspace; fail when none succeeded."""
    failed = _save_download_errors(workspace, run)
    if failed and len(failed) == run.files.count():
        raise RuntimeError(
            f"All {len(failed)} file(s) failed to download for "
            f"workspace {workspace.id}"
        )


def _record_outcome(task_id, error) -> None:
    """Record the outcome of a migration on the TaskResult of its export() task,
    which ended without one, as Celery would have."""
    if error is None:
        fields = {"status": states.SUCCESS, "result": json.dumps(True)}
    else:
        fields = {
            "status": states.FAILURE,
            "result": json.dumps(
                {
                    "exc_type": type(error).__name__,
                    "exc_message": [str(error)],
                    "exc_module": type(error).__module__,
                }
            ),
            "traceback": "".join(traceback.format_exception(error)),
        }
    # update() skips the auto_now of date_done.
    TaskResult.objects.filter(task_id=task_id).update(
        date_done=timezone.now(), **fields
    )


# From https://github.com/celery/django-celery-results/issues/286#issuecomment-1279161047
@before_task_publish.connect
def create_task_result_on_publish(sender=None, headers=None, body=None, **kwargs):  # pylint: disable=unused-argument
    # One row per migration: the tasks of its run have none.
    if headers.get("task") != export.name:
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


def _extra_task_of(task_id):
    task_result = TaskResult.objects.filter(task_id=task_id).first()
    if task_result is None:
        return None
    return ExtraTaskInfo.objects.filter(task_result=task_result).first()


def on_export_success(extra_task: ExtraTaskInfo) -> None:
    """Record a successful run and delete its local folder."""
    workspace = extra_task.workspace
    workspace.save()
    capture_migration_finished(extra_task, "success")

    cleanup_workspace_dir(workspace)


def on_export_failure(
    extra_task: ExtraTaskInfo, exception: BaseException | None = None
) -> None:
    """Mark the destinations still pending as failed, delete the local folder
    of the run and tell the user."""
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
        source_token_expired=isinstance(exception, SourceTokenExpired),
    )


@task_success.connect
def task_success(sender=None, **kwargs):  # pylint: disable=unused-argument
    # The outcome of a run is reported by finish_migration.
    if sender.name != export.name:
        return
    extra_task = _extra_task_of(sender.request.id)
    if extra_task is not None:
        on_export_success(extra_task)


@task_failure.connect
def task_failure(sender=None, **kwargs):
    if sender.name != export.name:
        return
    extra_task = _extra_task_of(sender.request.id)
    if extra_task is not None:
        on_export_failure(extra_task, kwargs.get("exception"))
