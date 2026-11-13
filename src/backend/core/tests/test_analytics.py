"""Tests for the PostHog analytics helper."""

from unittest.mock import patch

from django.test import override_settings

import pytest
from django_celery_results.models import TaskResult

from core import factories
from core.analytics import (
    posthog_capture,
    run_files_stats,
    save_run_files_stats,
    workspaces_counts,
)
from core.models import ExtraTaskInfo, MigrationFile, MigrationRun, Workspace

pytestmark = pytest.mark.django_db


@override_settings(POSTHOG_KEY=None)
@patch("core.analytics.posthog.capture")
def test_posthog_capture_noop_without_key(capture):
    posthog_capture("event", factories.UserFactory(), {"a": 1})
    capture.assert_not_called()


@override_settings(POSTHOG_KEY="key")
@patch("core.analytics.posthog.capture")
def test_posthog_capture_with_workspace(capture):
    user = factories.UserFactory(email="user@example.com")
    workspace = factories.WorkspaceFactory(
        destination_statuses={"drive": Workspace.Status.PENDING}
    )
    posthog_capture("event", user, {"a": 1}, workspace=workspace)
    capture.assert_called_once_with(
        "event",
        distinct_id="user@example.com",
        properties={
            "a": 1,
            "workspace_id": str(workspace.id),
            "destinations": ["drive"],
        },
    )


def _run(task_id="task-1"):
    return MigrationRun.objects.create(
        workspace=factories.WorkspaceFactory(), task_id=task_id
    )


def _downloaded(run, path, mime_type, size):
    return MigrationFile(
        run=run,
        position=run.files.count(),
        source={"id": path},
        path=path,
        status=MigrationFile.Status.DOWNLOADED,
        mime_type=mime_type,
        size=size,
    )


def test_run_files_stats():
    """Size and types of the files a run downloaded, grouped by extension and
    MIME type."""
    run = _run()
    for args in [
        ("report.PDF", "application/pdf", 9),
        ("sub/notes.txt", "text/plain", 5),
        ("sub/README", "text/plain", 2),
        ("sub/other.txt", "text/plain", 6),
    ]:
        _downloaded(run, *args).save()
    MigrationFile.objects.create(
        run=run,
        position=9,
        source={"id": "lost"},
        path="lost.txt",
        status=MigrationFile.Status.DOWNLOAD_FAILED,
    )

    assert run_files_stats(run) == {
        "workspace_size_bytes": 22,
        "file_types": [
            {"extension": "", "mime_type": "text/plain", "count": 1, "size_bytes": 2},
            {
                "extension": ".pdf",
                "mime_type": "application/pdf",
                "count": 1,
                "size_bytes": 9,
            },
            {
                "extension": ".txt",
                "mime_type": "text/plain",
                "count": 2,
                "size_bytes": 11,
            },
        ],
    }


def test_run_files_stats_without_downloaded_file():
    """A run that downloaded nothing weighs nothing."""
    assert run_files_stats(_run()) == {
        "workspace_size_bytes": 0,
        "file_types": [],
    }


def test_save_run_files_stats_keeps_them_on_the_task():
    """The stats outlive the run, purged before the task handlers send them."""
    run = _run()
    extra_task = ExtraTaskInfo.objects.create(
        task_result=TaskResult.objects.create(task_id=run.task_id),
        workspace=run.workspace,
        user=factories.UserFactory(),
    )
    _downloaded(run, "a.txt", "text/plain", 3).save()

    save_run_files_stats(run.task_id, run)

    extra_task.refresh_from_db()
    assert extra_task.files_stats["workspace_size_bytes"] == 3


def test_save_run_files_stats_without_task_does_not_raise():
    """Analytics must never fail a migration."""
    save_run_files_stats("unknown-task", _run())


def test_workspaces_counts():
    user = factories.UserFactory()
    user.workspaces.add(
        factories.WorkspaceFactory(),
        factories.WorkspaceFactory(status=Workspace.Status.SUCCESS),
        factories.WorkspaceFactory(status=Workspace.Status.FAILURE),
    )
    assert workspaces_counts(user) == {
        "workspaces_found": 3,
        "workspaces_not_migrated": 1,
    }
