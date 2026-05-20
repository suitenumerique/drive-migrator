from django.conf import settings

from django_celery_results.models import TaskResult
from rest_framework.response import Response
from rest_framework.views import APIView

from core.sources.osmose.serializers import WorkspaceSerializer

from ...destinations.drive.drive_backend import DriveUserTokenBackend
from ...destinations.resana.resana_backend import ResanaBackend
from ...models import ExtraTaskInfo, FeatureFlag, Workspace
from ...processing.tasks import export
from ...sources.resana.token_manager import ResanaTokenManager
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
        check_drive_token = False
        check_resana_source_token = False
        for workspace in workspaces:
            types = data.get(str(workspace.id))
            if "resana" in types:
                check_resana_user = True
            if "drive" in types:
                check_drive_token = True
            if workspace.source_type == "resana":
                check_resana_source_token = True

        if check_resana_user:
            resana_backend = ResanaBackend()
            resana_user = resana_backend.fetch_user(user)
            if not resana_user:
                raise APIException("ResanaUserNotFound")

        if (
            check_drive_token
            and getattr(settings, "DRIVE_AUTH_MODE", "service_account") == "user_token"
        ):
            try:
                DriveUserTokenBackend(user)._get_token()  # noqa: SLF001
            except Exception as exc:  # noqa: BLE001
                raise APIException("DriveTokenRequired") from exc

        if check_resana_source_token and not ResanaTokenManager(user).is_connected():
            raise APIException("ResanaTokenRequired")

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
