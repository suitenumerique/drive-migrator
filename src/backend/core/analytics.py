"""PostHog utilities."""

import os
from collections import Counter

from django.conf import settings

import posthog
from celery.utils.log import get_task_logger

from core.models import ExtraTaskInfo, MigrationFile, MigrationRun, Workspace

logger = get_task_logger(__name__)


def workspaces_counts(user) -> dict:
    """Counts used as PostHog person properties, refreshed on sync and migration end."""
    return {
        "workspaces_found": user.workspaces.count(),
        "workspaces_not_migrated": user.workspaces.filter(
            status=Workspace.Status.NONE
        ).count(),
    }


def run_files_stats(run: MigrationRun) -> dict:
    """Return the size and file types of what a migration run downloaded."""
    counts = Counter()
    sizes = Counter()
    for path, mime_type, size in run.files.filter(
        status=MigrationFile.Status.DOWNLOADED
    ).values_list("path", "mime_type", "size"):
        file_type = (os.path.splitext(path)[1].lower(), mime_type)
        counts[file_type] += 1
        sizes[file_type] += size
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


def save_run_files_stats(task_id: str, run: MigrationRun) -> None:
    """Keep the stats of a run on its task, for the task handlers to send them
    once the run is purged. Never raises: analytics must not fail a migration."""
    try:
        ExtraTaskInfo.objects.filter(task_result__task_id=task_id).update(
            files_stats=run_files_stats(run)
        )
    # Broad on purpose, see the docstring.
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Could not save the files stats of task %s", task_id)


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
