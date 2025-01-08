from rest_framework import serializers

from core.models import Workspace
from core.osmose.osmose_backend import WorkspaceStatusEnum


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
            "osmose_id",
            "status_resana",
            "status_archive",
            "resana_files_success",
            "resana_files_error",
        ]
