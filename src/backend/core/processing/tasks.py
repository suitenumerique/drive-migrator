from celery import states
from celery.signals import before_task_publish, task_failure, task_success
from celery.utils.log import get_task_logger
from django_celery_results.models import TaskResult

from core.mails_manager import MailsManager
from core.models import ExtraTaskInfo, User, Workspace
from core.osmose.osmose_backend import OsmoseManager
from core.processing.folder_creator import FolderCreator
from core.processing.folder_helper import ArchiveManager
from core.resana.resana_backend import ResanaBackend
from core.resana.s3_resana_manager import S3ResanaManager

from main.celery_app import app

logger = get_task_logger(__name__)


@app.task(bind=True)
def export(self, data):  # pylint: disable=unused-argument
    workspace_id = data["workspace"]["id"]
    logger.info(f"Starting workspace {workspace_id} ...")

    workspace = Workspace.objects.get(id=workspace_id)
    user = User.objects.get(id=data["user"]["id"])

    backend = OsmoseManager().get_backend()

    logger.info("Calling get_workspace_documents_structure ...")
    folder = backend.get_workspace_documents_structure(workspace)

    logger.info("Calling create_folder ...")
    creator = FolderCreator()
    creator.create_folder(workspace, folder)

    mails_manager = MailsManager()

    if workspace.status_archive == Workspace.Status.PENDING:
        logger.info("Calling zip_workspace_folder ...")
        helper = ArchiveManager()
        helper.zip_workspace_folder(workspace)

        logger.info("Calling upload_archive ...")
        archive_url = helper.upload_archive(workspace)

        logger.info(f"Sending send_archive_download_mail ${archive_url} ...")
        mails_manager.send_archive_download_mail(user, workspace, archive_url)
        workspace.status_archive = Workspace.Status.SUCCESS
        workspace.save()

    if workspace.status_resana == Workspace.Status.PENDING:
        resana_backend = ResanaBackend()
        logger.info("Calling resana create_workspace ...")
        resana_backend.create_workspace(workspace)
        workspace.status_resana = Workspace.Status.PENDING
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
    workspace.status = Workspace.Status.SUCCESS
    workspace.save()


@task_failure.connect
def task_failure(sender=None, **kwargs):
    task_result = TaskResult.objects.get(task_id=sender.request.id)
    extra_task = ExtraTaskInfo.objects.get(task_result=task_result)
    workspace = extra_task.workspace
    workspace.status = Workspace.Status.FAILURE

    if workspace.status_archive == Workspace.Status.PENDING:
        workspace.status_archive = Workspace.Status.FAILURE
    if workspace.status_resana == Workspace.Status.PENDING:
        workspace.status_resana = Workspace.Status.FAILURE
    workspace.save()
