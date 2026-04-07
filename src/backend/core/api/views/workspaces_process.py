from django_celery_results.models import TaskResult
from rest_framework.response import Response
from rest_framework.views import APIView

from core.sources.osmose.serializers import WorkspaceSerializer

from ...models import ExtraTaskInfo, FeatureFlag, Workspace
from ...processing.tasks import export
from ...destinations.resana.resana_backend import ResanaBackend
from ...utils import is_feature
from .. import APIException
from ..serializers import UserSerializer


def push_workspace_task(workspace, user):
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


class WorkspacesProcessAPIView(APIView):
    def create_export(self, user, workspace, types):
        if workspace.status != Workspace.Status.NONE:
            raise APIException("WorkspaceAlreadyExporting")

        # Set workspace status to pending.
        # IMPORTANT: Must be before the celery task creation because the
        # task use this data.
        workspace.migration_user = user
        for dest_name in types:
            workspace.set_destination_status(dest_name, Workspace.Status.PENDING)
        workspace.save()
        push_workspace_task(workspace, user)

    def validation(self, user, data, workspaces):
        """
        Here we want to make sure the user exist on Resana if we are going to export to Resana at least one workspace.
        """
        check_resana_user = False
        for workspace in workspaces:
            types = data.get(str(workspace.id))
            if "resana" in types:
                check_resana_user = True
                break

        if check_resana_user:
            resana_backend = ResanaBackend()
            resana_user = resana_backend.fetch_user(user)
            if not resana_user:
                raise APIException("ResanaUserNotFound")

    def post(self, request):
        if not is_feature(FeatureFlag.Name.ALLOW_NEW_TASKS):
            raise APIException("FeatureNotEnabled")

        user = request.user

        data = request.data.get("workspaces")
        # This way we can check if the user has access to the workspaces
        workspaces = user.workspaces.filter(id__in=[*data])
        if len(workspaces) != len(data):
            raise APIException("WorkspaceNotFound")

        self.validation(user, data, workspaces)

        # user.workspaces
        for workspace in workspaces:
            types = data.get(str(workspace.id))
            self.create_export(user, workspace, types)
        return Response()
