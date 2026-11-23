"""Organization admin reporting: context and workspace list."""

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from core.admin_reporting import (
    admin_reporting_is_enabled,
    admin_workspaces,
    eligible_count,
    ensure_workspace_admin,
    organization_stats,
    parse_list_params,
    require_organization,
)
from core.api import APIException
from core.api.admin_serializers import (
    AdminContextSerializer,
    AdminManagersSerializer,
    AdminStatsSerializer,
    AdminWorkspaceSerializer,
)
from core.api.permissions import IsAuthenticated
from core.models import Workspace


class AdminWorkspacePagination(PageNumberPagination):
    """Page sizes from the admin contract, not the global page of 1000."""

    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 100

    def __init__(self, eligible=0):
        super().__init__()
        self.eligible_count = eligible

    def get_paginated_response(self, data):
        return Response(
            {
                "count": self.page.paginator.count,
                "eligible_count": self.eligible_count,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": data,
            }
        )


class AdminContextAPIView(APIView):
    """Organizations the signed-in user administers."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        _ensure_admin_access()
        return Response(AdminContextSerializer(request.user).data)


class AdminWorkspacesAPIView(APIView):
    """Workspaces of one organization the user administers."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        _ensure_admin_access()
        params = parse_list_params(request.query_params)
        organization = require_organization(
            request.user, request.query_params.get("organization")
        )
        queryset = admin_workspaces(organization, params)
        paginator = AdminWorkspacePagination(eligible_count(queryset, params["status"]))
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            AdminWorkspaceSerializer(page, many=True).data
        )


class AdminStatsAPIView(APIView):
    """Organization-wide counts behind the indicator tiles."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        _ensure_admin_access()
        organization = require_organization(
            request.user, request.query_params.get("organization")
        )
        return Response(AdminStatsSerializer(organization_stats(organization)).data)


class AdminWorkspaceManagersAPIView(APIView):
    """Managers of one workspace, loaded when the admin opens the modal."""

    permission_classes = [IsAuthenticated]

    def get(self, request, workspace_id):
        _ensure_admin_access()
        try:
            workspace = Workspace.objects.select_related("organization").get(
                pk=workspace_id
            )
        except Workspace.DoesNotExist as exc:
            raise APIException("NotOrganizationAdmin") from exc
        ensure_workspace_admin(request.user, workspace)
        return Response(AdminManagersSerializer(workspace).data)


def _ensure_admin_access() -> None:
    """The flag is the only gate. Admin membership is read from the database.

    No Resana call: who administers what is stored on Organization.admins.
    """
    if not admin_reporting_is_enabled():
        raise APIException("FeatureNotEnabled")
