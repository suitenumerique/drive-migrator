from django.conf import settings
from django.http import Http404, HttpResponse
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
import requests
import time
import json
import boto3
from django_celery_results.models import TaskResult
from storages.backends.s3 import S3Storage

from core.mails_manager import MailsManager
from core.models import Workspace, ExtraTaskInfo
from core.osmose.osmose_backend import OsmoseFolder
from core.osmose.osmose_real_backend import OsmoseRealBackend
from core.processing.folder_creator import FolderCreator
from core.processing.folder_helper import ArchiveManager
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from core.processing.s3_resana_manager import S3ResanaManager
from django.contrib.sites.models import Site
from django.template.loader import render_to_string
from django.core import exceptions, mail

from ..serializers import UserSerializer
from ...osmose.serializers import WorkspaceSerializer
from ...processing.tasks import export


def create_export(user, workspace, types):

    # Create Celery task.
    result = export.delay(data={"workspace": WorkspaceSerializer(workspace).data, "user": UserSerializer(user).data})
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

    backend = OsmoseRealBackend()
    user = request.user

    workspace = Workspace.objects.get(osmose_id="c_2000365")
    create_export(user, workspace, ["resana", "archive"])

    return HttpResponse("dev")


    return HttpResponse("dev")

    # title = _("Invitation to join Impress!")
    # template_vars = {"title": title, "site": Site.objects.get_current(), "email": request.user.email, "workspace_name": workspace.title, "download_url": "https://www.google.com"}
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
    print("#####\nFOLDER\n#####")
    print_folder(folder, 0)

    print("#####\nCreating folder\n#####")
    creator = FolderCreator()
    creator.create_folder(workspace, folder)

    print("#####\nZipping folder\n#####")
    helper = ArchiveManager()
    helper.zip_workspace_folder(workspace)

    print("#####\nUploading zip to S3\n#####")
    archive_url = helper.upload_archive(workspace)
    print("archive_url", archive_url)

    mails_manager = MailsManager()
    mails_manager.send_archive_download_mail(user, workspace, archive_url)

    print("#####\nUploading files to S3\n#####")
    s3_manager = S3ResanaManager()
    s3_manager.upload_folder(workspace)
    mails_manager.send_resana_ready_mail(user, workspace)

    return HttpResponse("Dev")

def print_folder(folder: OsmoseFolder, level=0):

    print(" " * level + folder.name)
    for file in folder.files:
        print(" " * level + "  " + file.name)
    for child in folder.children:
        print_folder(child, level + 1)
