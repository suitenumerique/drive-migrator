"""Workspaces viewsets"""
from django.db.models import JSONField, OuterRef, Subquery
from django.db.models.functions import Lower
from django.forms.fields import UUIDField

from django_filters.rest_framework import DjangoFilterBackend, FilterSet
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from ...destinations.resana.resana_backend import ResanaBackend
from ...models import ExtraTaskInfo, Workspace
from ...processing.folder_helper import ArchiveManager
from ...sources.osmose.serializers import WorkspaceSerializer
from ..filters import MultipleValueFilter
from ..permissions import IsAuthenticated


class WorkspacesFilterSet(FilterSet):
    """FilterSet for Workspaces."""

    id = MultipleValueFilter(field_class=UUIDField)

    class Meta:
        model = Workspace
        fields = ["id"]


def with_latest_integrity(queryset):
    """Annotate each workspace with the integrity counts of its latest run,
    read from the database instead of loading every report."""
    latest_run = ExtraTaskInfo.objects.filter(workspace=OuterRef("pk")).order_by("-id")

    def latest(field):
        return Subquery(latest_run.values(field)[:1], output_field=JSONField())

    return queryset.annotate(
        integrity_migrated=latest("integrity_report__summary__migrated_files_count"),
        integrity_source=latest("integrity_report__summary__source_files_count"),
        integrity_check_passed=Subquery(
            latest_run.values("integrity_check_passed")[:1]
        ),
    )


class WorkspacesViewset(viewsets.ReadOnlyModelViewSet):  # pylint: disable=too-many-ancestors
    """Viewset for Workspaces."""

    serializer_class = WorkspaceSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_class = WorkspacesFilterSet

    def get_queryset(self):
        user = self.request.user
        # Stable pagination; the frontend groups workspaces by status itself.
        return with_latest_integrity(user.workspaces.all()).order_by(
            "status", Lower("title")
        )

    @action(detail=True)
    def download_archive(self, request, *args, **kwargs):
        helper = ArchiveManager()
        workspace = self.get_object()
        return Response({"url": helper.get_download_url(workspace)})

    @action(detail=True)
    def resana_error_details(self, request, *args, **kwargs):
        workspace = self.get_object()
        backend = ResanaBackend()
        details = backend.get_error_details(workspace)
        job_data = backend.fetch_job(workspace)
        return Response({"details": details, "job": job_data})

    @action(detail=True)
    def resana_retry(self, request, *args, **kwargs):
        workspace = self.get_object()
        backend = ResanaBackend()
        data = backend.retry_job(workspace)
        return Response({"data": data})
