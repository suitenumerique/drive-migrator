"""Workspaces viewsets"""
from django.forms.fields import UUIDField

from django_filters.rest_framework import DjangoFilterBackend, FilterSet
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from ...models import Workspace
from ...sources.osmose.serializers import WorkspaceSerializer
from ...processing.folder_helper import ArchiveManager
from ...resana.resana_backend import ResanaBackend
from ..filters import MultipleValueFilter


class WorkspacesFilterSet(FilterSet):
    """FilterSet for Workspaces."""

    id = MultipleValueFilter(field_class=UUIDField)

    class Meta:
        model = Workspace
        fields = ["id"]


class WorkspacesViewset(viewsets.ReadOnlyModelViewSet):  # pylint: disable=too-many-ancestors
    """Viewset for Workspaces."""

    serializer_class = WorkspaceSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = WorkspacesFilterSet

    def get_queryset(self):
        user = self.request.user
        return user.workspaces.all()

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
