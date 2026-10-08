"""PostHog utilities."""

import os
from collections import Counter

from django.conf import settings

import magic
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


def local_files_stats(path) -> dict:
    """Return the size and file types of a local workspace folder."""
    if not os.path.isdir(path):
        return {}
    counts = Counter()
    sizes = Counter()
    for root, _, filenames in os.walk(path):
        for filename in filenames:
            file_path = os.path.join(root, filename)
            file_type = (
                os.path.splitext(filename)[1].lower(),
                magic.from_file(file_path, mime=True),
            )
            counts[file_type] += 1
            sizes[file_type] += os.path.getsize(file_path)
    return {
        "workspace_size_bytes": sum(sizes.values()),
        "file_types": [
            {
                "extension": extension,
                "mime_type": mime_type,
                "count": counts[extension, mime_type],
                "size_bytes": sizes[extension, mime_type],
            }
            for extension, mime_type in sorted(counts)
        ],
    }


def posthog_capture(event_name, user, properties=None, workspace=None):
    """Capture an event with PostHog. No-op when POSTHOG_KEY is not set."""
    if not settings.POSTHOG_KEY:
        return
    properties = dict(properties) if properties is not None else {}
    if workspace is not None:
        properties["workspace_id"] = str(workspace.id)
        properties["destinations"] = sorted(workspace.destination_statuses)
    posthog.capture(
        event_name,
        distinct_id=getattr(user, "email", None),
        properties=properties,
    )
