# pylint: skip-file
"""Development views."""
from django.conf import settings
from django.http import Http404, HttpResponse

from core.models import Workspace
from core.osmose.osmose_backend import OsmoseFolder, OsmoseManager

from ...osmose.osmose_real_backend import PageWalker
from ...osmose.serializers import WorkspaceSerializer
from ...processing.tasks import export
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


# def debug_folder(folder: OsmoseFolder):
#     print("Debugging folder")
#
#     def aux(folder: OsmoseFolder, depth=0):
#         print(" " * depth + folder.name)
#         for child in folder.children:
#             aux(child, depth + 1)
#
#     aux(folder)


def dev_view(request):
    if not settings.DEBUG:
        raise Http404()

    workspace = Workspace.objects.get(id="647368dd-96ad-4240-b690-4ebd271d69bc")

    backend = OsmoseManager().get_backend()
    users = backend.fetch_users(workspace)

    return HttpResponse("Dev")


def print_folder(folder: OsmoseFolder, level=0):
    print(" " * level + folder.name)  # noqa: T201
    for file in folder.files:
        print(" " * level + "  " + file.name)  # noqa: T201
    for child in folder.children:
        print_folder(child, level + 1)
