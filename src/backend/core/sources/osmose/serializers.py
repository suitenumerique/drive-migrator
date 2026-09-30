from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from core.models import Workspace


class WorkspaceIntegritySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """File counts and result of the integrity check of the latest run."""

    migrated_files_count = serializers.IntegerField()
    source_files_count = serializers.IntegerField()
    # None when no destination was checked file by file (e.g. archive).
    sent_files_count = serializers.IntegerField(allow_null=True)
    check_passed = serializers.BooleanField(allow_null=True)


class WorkspaceSerializer(serializers.ModelSerializer):
    integrity = serializers.SerializerMethodField()

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
            "is_truncated",
            "integrity",
        ]

    @extend_schema_field(WorkspaceIntegritySerializer(allow_null=True))
    def get_integrity(self, obj):
        """None unless the queryset was annotated by with_latest_integrity() and
        the latest run has an integrity report."""
        migrated = getattr(obj, "integrity_migrated", None)
        if migrated is None:
            return None
        return {
            "migrated_files_count": migrated,
            "source_files_count": obj.integrity_source,
            "sent_files_count": obj.integrity_sent,
            "check_passed": obj.integrity_check_passed,
        }
