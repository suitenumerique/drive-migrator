import logging

from django_celery_results.models import TaskResult
from rest_framework.response import Response
from rest_framework.views import APIView

from core.analytics import posthog_capture
from core.sources.osmose.serializers import WorkspaceSerializer

from ...backends.destination import AccountPolicy, DestinationRegistry
from ...destinations.drive.drive_backend import (
    is_user_token_auth_mode,
    make_user_drive_backend,
)
from ...destinations.resana.resana_backend import ResanaBackend
from ...models import ExtraTaskInfo, FeatureFlag, Workspace
from ...processing.tasks import export
from ...processing.token_status import log_token_status
from ...sources.resana.token_manager import ResanaTokenManager
from ...utils import is_feature
from .. import APIException
from ..destination_accounts import check_user_account
from ..serializers import UserSerializer

logger = logging.getLogger(__name__)


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
        posthog_capture("migration_started", user, workspace=workspace)

    @staticmethod
    def ensure_drive_token(user):
        """Make sure the user's Drive token (or exchanged token) can be obtained."""
        try:
            # pylint: disable-next=protected-access
            make_user_drive_backend(user)._get_token()  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            raise APIException("DriveTokenRequired") from exc

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

        log_token_status(
            logger,
            "Before migration launch",
            user,
            resana=check_resana_source_token,
            drive=check_drive_token,
        )

        if check_resana_user:
            resana_backend = ResanaBackend()
            resana_user = resana_backend.fetch_user(user)
            if not resana_user:
                raise APIException("ResanaUserNotFound")

        if check_drive_token and is_user_token_auth_mode():
            self.ensure_drive_token(user)

        if check_resana_source_token and not ResanaTokenManager(user).is_connected():
            raise APIException("ResanaTokenRequired")

        # Without the account, the destination refuses the run on its first call.
        dest_names = {
            name for workspace in workspaces for name in data.get(str(workspace.id))
        }
        for dest in DestinationRegistry.get_all():
            if (
                dest.name in dest_names
                and dest.get_account_policy() == AccountPolicy.USER_CREATED
                and not check_user_account(dest, user)
            ):
                raise APIException("AccountNotFound")

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
