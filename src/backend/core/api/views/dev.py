# pylint: skip-file
"""Development views."""
from django.conf import settings
from django.http import Http404, HttpResponse

from django_celery_results.models import TaskResult

from core.mails_manager import MailsManager
from core.models import ExtraTaskInfo, Workspace
from core.osmose.osmose_backend import OsmoseFolder
from core.processing.folder_creator import FolderCreator
from core.processing.folder_helper import ArchiveManager
from core.resana.s3_resana_manager import S3ResanaManager

from ...osmose.serializers import WorkspaceSerializer
from ...processing.tasks import export
from ...resana.resana_backend import ResanaBackend
from ..serializers import UserSerializer


def create_export(user, workspace, types):
    # Create Celery task.
    result = export.delay(
        data={
            "workspace": WorkspaceSerializer(workspace).data,
            "user": UserSerializer(user).data,
        }
    )
    # Fetch task from db created by django-celery-results.
    dbResult = TaskResult.objects.get(task_id=result.id)
    # Create extra task with information required for querying.
    extraTask = ExtraTaskInfo()
    extraTask.workspace = workspace
    extraTask.task_result = dbResult
    extraTask.save()

    workspace.status = Workspace.Status.PENDING
    if "resana" in types:
        workspace.status_resana = Workspace.Status.PENDING
    if "archive" in types:
        workspace.status_archive = Workspace.Status.PENDING
    workspace.save()


def dev_view(request):
    if not settings.DEBUG:
        raise Http404()

    workspace = Workspace.objects.get(id="c32241f0-6c11-4ab0-a1ff-a922c35a7937")

    resana_backend = ResanaBackend()
    resana_backend.create_workspace(workspace)

    return HttpResponse("dev")

    # title = _("Invitation to join Impress!")
    # template_vars = {"title": title, "site": Site.objects.get_current(), "email": request.user.email,
    # "workspace_name": workspace.title, "download_url": "https://www.google.com"}
    # msg_html = render_to_string("mail/html/archive_download.html", template_vars)
    # msg_plain = render_to_string("mail/text/archive_download.txt", template_vars)
    # print(request.user.email)
    # mail.send_mail(
    #     title,
    #     msg_plain,
    #     settings.EMAIL_FROM,
    #     [request.user.email],
    #     html_message=msg_html,
    #     fail_silently=False,
    # )
    #
    # return HttpResponse("Dev")

    folder = backend.get_workspace_documents_structure(workspace)
    print("#####\nFOLDER\n#####")  # noqa: T201
    print_folder(folder, 0)

    print("#####\nCreating folder\n#####")  # noqa: T201
    creator = FolderCreator()
    creator.create_folder(workspace, folder)

    print("#####\nZipping folder\n#####")  # noqa: T201
    helper = ArchiveManager()
    helper.zip_workspace_folder(workspace)

    print("#####\nUploading zip to S3\n#####")  # noqa: T201
    archive_url = helper.upload_archive(workspace)
    print("archive_url", archive_url)  # noqa: T201

    mails_manager = MailsManager()
    mails_manager.send_archive_download_mail(user, workspace, archive_url)

    print("#####\nUploading files to S3\n#####")  # noqa: T201
    s3_manager = S3ResanaManager()
    s3_manager.upload_folder(workspace)
    mails_manager.send_resana_ready_mail(user, workspace)

    return HttpResponse("Dev")


def print_folder(folder: OsmoseFolder, level=0):
    print(" " * level + folder.name)  # noqa: T201
    for file in folder.files:
        print(" " * level + "  " + file.name)  # noqa: T201
    for child in folder.children:
        print_folder(child, level + 1)
