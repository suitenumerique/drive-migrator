"""Permission handlers for the core app."""
from rest_framework import permissions

from core.api import APIException

ACTION_FOR_METHOD_TO_PERMISSION = {
    "versions_detail": {"DELETE": "versions_destroy", "GET": "versions_retrieve"}
}


class IsAuthenticated(permissions.BasePermission):
    """
    Allows access only to authenticated users. Alternative method checking the presence
    of the auth token to avoid hitting the database.
    """

    def has_permission(self, request, view):
        return bool(request.auth) or request.user.is_authenticated


class IsSelf(IsAuthenticated):
    """
    Allows access only to authenticated users. Alternative method checking the presence
    of the auth token to avoid hitting the database.
    """

    def has_object_permission(self, request, view, obj):
        """Write permissions are only allowed to the user itself."""
        return obj == request.user


class AdminReportingEnabled(IsAuthenticated):
    """Signed-in user and an active admin-reporting flag.

    A missing or inactive flag is FeatureNotEnabled, not a generic 403.
    The flag check is imported lazily: admin_reporting imports workspace
    views, which import this module.
    """

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        # pylint: disable-next=import-outside-toplevel
        from core.admin_reporting import admin_reporting_is_enabled

        if not admin_reporting_is_enabled():
            raise APIException("FeatureNotEnabled")
        return True
