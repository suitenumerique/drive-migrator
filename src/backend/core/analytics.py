"""PostHog utilities."""

from django.conf import settings

import posthog

from core.models import Workspace


def workspaces_counts(user) -> dict:
    """Counts used as PostHog person properties, refreshed on sync and migration end."""
    return {
        "workspaces_found": user.workspaces.count(),
        "workspaces_not_migrated": user.workspaces.filter(
            status=Workspace.Status.NONE
        ).count(),
    }


def posthog_capture(event_name, user, properties=None, workspace=None):
    """Capture an event with PostHog. No-op when POSTHOG_KEY is not set."""
    if not settings.POSTHOG_KEY:
        return
    properties = dict(properties or {})
    if workspace is not None:
        properties["workspace_id"] = str(workspace.id)
        properties["destinations"] = sorted(workspace.destination_statuses)
    posthog.capture(
        event_name,
        distinct_id=getattr(user, "email", None),
        properties=properties,
    )
