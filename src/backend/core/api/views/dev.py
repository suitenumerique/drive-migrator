# pylint: skip-file
"""Development views."""
from django.conf import settings
from django.http import Http404, HttpResponse

from django_celery_results.models import TaskResult

from core.mails_manager import MailsManager
from core.models import ExtraTaskInfo, Workspace
from core.osmose.osmose_backend import OsmoseFolder, OsmoseManager
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

    workspace.save()


def dev_view(request):
    if not settings.DEBUG:
        raise Http404()

    1 / 0

    return HttpResponse("Dev")


def print_folder(folder: OsmoseFolder, level=0):
    print(" " * level + folder.name)  # noqa: T201
    for file in folder.files:
        print(" " * level + "  " + file.name)  # noqa: T201
    for child in folder.children:
        print_folder(child, level + 1)
