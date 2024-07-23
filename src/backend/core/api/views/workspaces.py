from django_filters.rest_framework import DjangoFilterBackend, FilterSet
from django.forms.fields import UUIDField
from ..filters import MultipleValueFilter
from ...models import Workspace
from ...osmose.serializers import WorkspaceSerializer
from rest_framework import viewsets
from rest_framework.response import Response

class WorkspacesFilterSet(FilterSet):
    id = MultipleValueFilter(field_class=UUIDField)

    class Meta:
        model = Workspace
        fields = ["id"]

class WorkspacesViewset(viewsets.ReadOnlyModelViewSet):

    serializer_class = WorkspaceSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = WorkspacesFilterSet

    def get_queryset(self):
        user = self.request.user
        return user.workspaces.all()

