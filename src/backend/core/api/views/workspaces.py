"""Workspaces viewsets"""
from django.db.models import JSONField, OuterRef, Subquery
from django.db.models.functions import Lower
from django.forms.fields import UUIDField
from django.http import StreamingHttpResponse
from django.urls import reverse
from django.utils.http import content_disposition_header

from django_filters.rest_framework import DjangoFilterBackend, FilterSet
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, Throttled, ValidationError
from rest_framework.response import Response

from ...backends.destination import DestinationRegistry
from ...destinations.archive.download import (
    SlotStream,
    acquire_download_slot,
    archive_stream,
    get_manifest_key,
    read_manifest,
    release_download_slot,
)
from ...destinations.resana.resana_backend import ResanaBackend
from ...models import ExtraTaskInfo, Workspace
from ...processing.folder_helper import ArchiveManager
from ...processing.rejected_files import RejectedFilesArchive
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
        integrity_sent=latest("integrity_report__summary__sent_files_count"),
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
        workspace = self.get_object()
        if get_manifest_key(workspace):
            url = reverse("workspaces-archive-zip", kwargs={"pk": workspace.pk})
            return Response({"url": request.build_absolute_uri(url)})
        # Zip built by the former export, served by S3 itself.
        helper = ArchiveManager()
        return Response({"url": helper.get_download_url(workspace)})

    @extend_schema(responses={(200, "application/zip"): OpenApiTypes.BINARY})
    @action(detail=True)
    def archive_zip(self, request, *args, **kwargs):
        """Stream the zip of the workspace's archive, built from its S3 manifest."""
        workspace = self.get_object()
        manifest_key = get_manifest_key(workspace)
        if not manifest_key:
            raise NotFound("This workspace has no archive to stream.")
        if not acquire_download_slot():
            raise Throttled(
                wait=60, detail="Too many archive downloads at once, retry later."
            )
        try:
            stream = archive_stream(read_manifest(manifest_key))
        except BaseException:
            release_download_slot()
            raise

        response = StreamingHttpResponse(
            SlotStream(stream), content_type="application/zip"
        )
        response["Content-Length"] = len(stream)
        filename = workspace.title.replace("/", "_").replace("\\", "_")
        response["Content-Disposition"] = content_disposition_header(
            True, f"{filename}.zip"
        )
        # A buffering proxy would hold the whole zip on its disk.
        response["X-Accel-Buffering"] = "no"
        return response

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "destination",
                str,
                required=True,
                description="Name of the destination whose refused files are wanted.",
            )
        ]
    )
    @action(detail=True)
    def download_rejected_files(self, request, *args, **kwargs):
        workspace = self.get_object()
        try:
            destination = DestinationRegistry.get(
                request.query_params.get("destination", "")
            )
        except ValueError as error:
            raise ValidationError({"destination": str(error)}) from error
        url = RejectedFilesArchive(workspace, destination).get_download_url()
        if url is None:
            raise NotFound("This destination refused no file in the last migration.")
        return Response({"url": url})

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
