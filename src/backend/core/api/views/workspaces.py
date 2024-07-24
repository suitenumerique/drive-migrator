"""Workspaces viewsets"""
from django.forms.fields import UUIDField

from django_filters.rest_framework import DjangoFilterBackend, FilterSet
from rest_framework import viewsets

from ...models import Workspace
from ...osmose.serializers import WorkspaceSerializer
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
