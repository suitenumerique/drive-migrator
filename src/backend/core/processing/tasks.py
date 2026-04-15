import os

from django.conf import settings

from celery import states
from celery.signals import before_task_publish, task_failure, task_success
from celery.utils.log import get_task_logger
from django_celery_results.models import TaskResult

from core.backends.destination import DestinationRegistry
from core.backends.source import SourceFolder, SourceManager
from core.mails_manager import MailsManager
from core.models import ExtraTaskInfo, User, Workspace
from core.processing.folder_creator import FolderCreator
from core.processing.folder_helper import ArchiveManager
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
    archive_manager = ArchiveManager()
    archive_manager.delete_archive(workspace)
    logger.info("Cleaned up %s directory !", workspace.id)
    list_work_dir()


def debug_folder(folder: SourceFolder):
    logger.info("Debugging folder")

    def aux(f: SourceFolder, depth=0):
        logger.info(" " * depth + f.name)
        for child in f.children:
            aux(child, depth + 1)

    aux(folder)


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

    source_backend = SourceManager().get_backend()
    list_work_dir()

    logger.info("Calling get_workspace_structure ...")
    folder = source_backend.get_workspace_structure(workspace)
    debug_folder(folder)

    logger.info("Calling create_folder ...")
    creator = FolderCreator()
    local_path = creator.create_folder(workspace, folder, source_backend)

    list_workspace_dir(workspace)

    logger.info("Calling prepare_export ...")
    source_backend.prepare_export(workspace, local_path)

    for dest_backend in DestinationRegistry.get_all():
        dest_name = dest_backend.name
        logger.info(
            "%s status = %s", dest_name, workspace.get_destination_status(dest_name)
        )
        if workspace.get_destination_status(dest_name) == Workspace.Status.PENDING:
            logger.info("Calling %s export ...", dest_name)
            dest_backend.export(workspace, user, local_path)

    logger.info("Task done")

    return True


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

    cleanup_workspace_dir(workspace)

    mail_manager = MailsManager()
    mail_manager.send_fail_mail(extra_task.user, workspace)
