import os

from django.conf import settings

from celery import states
from celery.signals import before_task_publish, task_failure, task_success
from celery.utils.log import get_task_logger
from django_celery_results.models import TaskResult

from core.mails_manager import MailsManager
from core.models import ExtraTaskInfo, User, Workspace
from core.osmose.osmose_backend import OsmoseFolder, OsmoseManager
from core.processing.folder_creator import FolderCreator
from core.processing.folder_helper import ArchiveManager
from core.resana.resana_backend import ResanaBackend
from core.utils import get_dir_size, sizeof_fmt

from main.celery_app import app

logger = get_task_logger(__name__)


def list_work_dir():
    logger.info("Listing work dir")
    with os.scandir(settings.APP_WORK_DIR) as it:
        for entry in it:
            size = 0
            if entry.is_dir():
                size = get_dir_size(entry.path)
            elif entry.is_file():
                size = entry.stat().st_size
            size_fmt = sizeof_fmt(size)
            logger.info(f"{entry.name} {size_fmt} ({size})")


def list_workspace_dir(workspace: Workspace):
    logger.info(f"Listing workspace dir")
    creator = FolderCreator()
    path = creator.get_workspace_path(workspace)
    files = []
    for root, _, filenames in os.walk(path):
        for filename in filenames:
            files.append(os.path.join(root, filename))

    logger.info(f"Listing {len(files)} files")
    for file in files:
        size = os.stat(file).st_size
        size_formatted = sizeof_fmt(size)
        logger.info(f"File: {file} {size_formatted} ({size}) ...")


def cleanup_workspace_dir(workspace: Workspace):
    logger.info(f"Cleaning up {workspace.id} directory ...")
    creator = FolderCreator()
    creator.delete_folder(workspace)
    archive_manager = ArchiveManager()
    archive_manager.delete_archive(workspace)
    logger.info(f"Cleaned up {workspace.id} directory !")
    list_work_dir()


def debug_folder(folder: OsmoseFolder):
    logger.info("Debugging folder")

    def aux(folder: OsmoseFolder, depth=0):
        logger.info(" " * depth + folder.name)
        for child in folder.children:
            aux(child, depth + 1)

    aux(folder)


@app.task(bind=True)
def export(self, data):  # pylint: disable=unused-argument
    workspace_id = data["workspace"]["id"]
    logger.info(f"Starting workspace {workspace_id} ...")

    workspace = Workspace.objects.get(id=workspace_id)
    logger.info(
        f"Workspace title: {workspace.title}, status_archive: {workspace.status_archive}, status_resana: {workspace.status_resana}"
    )
    user = User.objects.get(id=data["user"]["id"])

    backend = OsmoseManager().get_backend()
    list_work_dir()

    logger.info("Calling get_workspace_documents_structure ...")
    folder = backend.get_workspace_documents_structure(workspace)
    debug_folder(folder)

    logger.info("Calling create_folder ...")
    creator = FolderCreator()
    creator.create_folder(workspace, folder)

    logger.info("Calling create_users_csv ...")
    backend.create_users_csv(workspace)

    list_workspace_dir(workspace)

    mails_manager = MailsManager()

    logger.info(f"status_archive = {workspace.status_archive}")
    if workspace.status_archive == Workspace.Status.PENDING:
        logger.info("Calling zip_workspace_folder ...")
        helper = ArchiveManager()
        helper.zip_workspace_folder(workspace)

        logger.info("Calling upload_archive ...")
        archive_url = helper.upload_archive(workspace)

        logger.info(f"Sending send_archive_download_mail ${archive_url} ...")
        mails_manager.send_archive_download_mail(user, workspace, archive_url)
        workspace.set_status_archive(Workspace.Status.SUCCESS)
        workspace.save()

    logger.info(f"status_resana = {workspace.status_resana}")
    if workspace.status_resana == Workspace.Status.PENDING:
        resana_backend = ResanaBackend()
        logger.info("Calling resana create_workspace ...")
        resana_backend.create_workspace(workspace, user)
        # At this point, this is the resana refresh job command that will put the workspace in success state
        workspace.set_status_resana(Workspace.Status.PENDING)
        workspace.save()

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
    task_result = TaskResult.objects.get(task_id=sender.request.id)
    extra_task = ExtraTaskInfo.objects.get(task_result=task_result)
    workspace = extra_task.workspace
    workspace.save()

    cleanup_workspace_dir(workspace)


@task_failure.connect
def task_failure(sender=None, **kwargs):
    task_result = TaskResult.objects.get(task_id=sender.request.id)
    extra_task = ExtraTaskInfo.objects.get(task_result=task_result)
    workspace = extra_task.workspace

    if workspace.status_archive == Workspace.Status.PENDING:
        workspace.set_status_archive(Workspace.Status.FAILURE)
    if workspace.status_resana == Workspace.Status.PENDING:
        workspace.set_status_resana(Workspace.Status.FAILURE)
    workspace.save()

    cleanup_workspace_dir(workspace)

    mail_manager = MailsManager()
    mail_manager.send_fail_mail(extra_task.user, workspace)
