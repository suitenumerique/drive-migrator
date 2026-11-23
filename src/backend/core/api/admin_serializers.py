"""Declared shapes for the organization admin API.

The counts, filters and derived statuses stay in core.admin_reporting.
These serializers only name each JSON field and convert its type.
"""

from rest_framework import serializers

from core.admin_reporting import display_status, manager_rows, workspace_queue


class AdminWorkspaceSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One workspace row. status, queue, target and origin are derived.

    status: NONE, PENDING, SUCCESS, GAP, FAILURE, LOCKED.
    queue: running, queued, or null.
    target: drive, archive, or null.
    origin: manager, admin, or null.
    """

    id = serializers.UUIDField()
    title = serializers.CharField()
    status = serializers.SerializerMethodField()
    queue = serializers.SerializerMethodField()
    target = serializers.SerializerMethodField()
    origin = serializers.SerializerMethodField()
    connected_count = serializers.IntegerField()
    managers_count = serializers.IntegerField()
    source_files_count = serializers.IntegerField(
        allow_null=True, source="integrity_source"
    )
    migrated_files_count = serializers.IntegerField(
        allow_null=True, source="integrity_migrated"
    )
    migrated_at = serializers.DateTimeField(allow_null=True)

    def get_status(self, workspace):
        """NONE, PENDING, SUCCESS, GAP, FAILURE or LOCKED."""
        return display_status(workspace)

    def get_queue(self, workspace):
        """running, queued, or null."""
        return workspace_queue(workspace)

    def get_target(self, workspace):
        """drive, archive, or null."""
        return workspace.admin_target

    def get_origin(self, workspace):
        """manager when a migration has run, otherwise null."""
        if workspace.has_run:
            return "manager"
        return None


class AdminStatsSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Tile counts for one organization.

    The instance is the dict returned by organization_stats. Field names
    match its keys, so no source is required.
    """

    last_sync = serializers.DateTimeField(allow_null=True)
    resana = serializers.IntegerField()
    connected = serializers.IntegerField()
    not_migrated = serializers.IntegerField()
    locked = serializers.IntegerField()
    drive = serializers.IntegerField()
    archive = serializers.IntegerField()
    pending = serializers.IntegerField()
    failure = serializers.IntegerField()


class AdminManagerSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One animator in the managers modal."""

    name = serializers.CharField(allow_blank=True)
    email = serializers.CharField(allow_blank=True)
    connected = serializers.BooleanField()
    last_login = serializers.DateTimeField(allow_null=True)


class AdminManagersSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Managers of one workspace. The instance is the workspace itself."""

    connected_count = serializers.SerializerMethodField()
    managers_count = serializers.SerializerMethodField()
    results = serializers.SerializerMethodField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._rows = None

    def _cached_rows(self, workspace):
        """One member query per response, shared by the three fields."""
        if self._rows is None:
            self._rows = manager_rows(workspace)
        return self._rows

    def get_connected_count(self, workspace):
        return sum(1 for row in self._cached_rows(workspace) if row["connected"])

    def get_managers_count(self, workspace):
        return len(self._cached_rows(workspace))

    def get_results(self, workspace):
        return AdminManagerSerializer(self._cached_rows(workspace), many=True).data


class AdminOrganizationSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """An organization the user administers. id is the portal source id, not the PK."""

    id = serializers.CharField(source="source_id")
    name = serializers.CharField(allow_blank=True)
    last_sync = serializers.DateTimeField(allow_null=True)
    can_backup = serializers.BooleanField()


class AdminContextFeaturesSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Flags the admin screen is allowed to read."""

    admin_reporting = serializers.BooleanField()


class AdminContextUserSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """The signed-in user. name stays null until it is stored."""

    name = serializers.SerializerMethodField()
    email = serializers.CharField(allow_null=True, allow_blank=True)

    def get_name(self, _user):
        """The contract has no stored display name yet."""
        return None


class AdminContextSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Who is signed in, and which organizations they administer.

    The instance is the user. Organizations are read from that user.
    """

    is_organization_admin = serializers.BooleanField()
    features = AdminContextFeaturesSerializer()
    user = AdminContextUserSerializer()
    organizations = AdminOrganizationSerializer(many=True)

    def to_representation(self, instance):
        organizations = instance.administered_organizations.order_by(
            "name", "source_id"
        )
        return super().to_representation(
            {
                "is_organization_admin": organizations.exists(),
                "features": {"admin_reporting": True},
                "user": instance,
                "organizations": organizations,
            }
        )


class AdminSynchronizeSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Identifier of the workspace-sync task that was queued.

    The instance is the task id string.
    """

    task_id = serializers.CharField()

    def to_representation(self, instance):
        return {"task_id": self.fields["task_id"].to_representation(instance)}
