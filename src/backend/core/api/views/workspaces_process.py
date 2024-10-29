from django_celery_results.models import TaskResult
from rest_framework.response import Response
from rest_framework.views import APIView

from core.osmose.serializers import WorkspaceSerializer

from ...models import ExtraTaskInfo, FeatureFlag, Workspace
from ...processing.tasks import export
from ...utils import is_feature
from .. import APIException
from ..serializers import UserSerializer


class WorkspacesProcessAPIView(APIView):
    def create_export(self, user, workspace, types):
        if workspace.status != Workspace.Status.NONE:
            raise APIException("WorkspaceAlreadyExporting")

        # Set workspace status to pending.
        # IMPORTANT: Must be before the celery task creation because the
        # task use this data.
        workspace.migration_user = user
        if "resana" in types:
            workspace.set_status_resana(Workspace.Status.PENDING)
        if "archive" in types:
            workspace.set_status_archive(Workspace.Status.PENDING)
        workspace.save()

        # Create Celery task.
        result = export.delay(
            data={
                "workspace": WorkspaceSerializer(workspace).data,
                "user": UserSerializer(user).data,
            }
        )
        # Fetch task from db created by django-celery-results.
        db_result = TaskResult.objects.get(task_id=result.id)
        # Create extra task with information required for querying.
        extra_task = ExtraTaskInfo()
        extra_task.workspace = workspace
        extra_task.task_result = db_result
        extra_task.user = user
        extra_task.save()

    def post(self, request):
        if not is_feature(FeatureFlag.Name.ALLOW_NEW_TASKS):
            raise APIException("FeatureNotEnabled")

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
