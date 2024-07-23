from django_celery_results.models import TaskResult
from .. import APIException
from rest_framework.views import APIView

from core.osmose.serializers import WorkspaceSerializer
from ..serializers import UserSerializer
from ...models import ExtraTaskInfo, Workspace
from ...processing.tasks import export
from rest_framework.response import Response


class WorkspacesProcessAPIView(APIView):

    def create_export(self, user, workspace, types):
        if workspace.status != Workspace.Status.NONE:
            raise APIException("WorkspaceAlreadyExporting")
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

    def post(self, request):
        data = request.data.get("workspaces")
        # This way we can check if the user has access to the workspaces
        workspaces = request.user.workspaces.filter(id__in=[*data])
        if len(workspaces) != len(data):
            raise APIException("WorkspaceNotFound")

        # user.workspaces
        for workspace in workspaces:
            types = data.get(str(workspace.id))
            self.create_export(request.user, workspace, types)
        return Response()
