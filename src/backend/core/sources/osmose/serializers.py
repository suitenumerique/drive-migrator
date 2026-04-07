from rest_framework import serializers

from core.models import Workspace
from core.sources.osmose.osmose_backend import WorkspaceStatusEnum


# Still used ?
class OsmoseWorkspaceSerializer(serializers.Serializer):
    title = serializers.CharField()
    id = serializers.CharField()
    status = serializers.SerializerMethodField()

    def get_status(self, obj):
        # Convert enum to its value
        return obj.status.value if obj.status else None


class WorkspaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Workspace
        fields = [
            "id",
            "title",
            "status",
            "source_id",
            "source_type",
            "destination_statuses",
            "destination_metadata",
        ]
