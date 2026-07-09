from rest_framework import serializers

from core.models import Workspace


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
            "files_limited",
        ]
