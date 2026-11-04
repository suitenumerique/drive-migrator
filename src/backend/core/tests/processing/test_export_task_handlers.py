"""Tests for the helpers and signal handlers around the export Celery task."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.utils import timezone

import pytest
from django_celery_results.models import TaskResult

from core.backends.source import SourceFolder
from core.models import ExtraTaskInfo, Workspace
from core.processing.tasks import (
    cleanup_workspace_dir,
    create_task_result_on_publish,
    debug_folder,
    export,
    list_work_dir,
    list_workspace_dir,
)
from core.processing.tasks import (
    task_failure as task_failure_handler,
)
from core.processing.tasks import (
    task_success as task_success_handler,
)
from core.sources.resana.token_manager import ResanaTokenExpired

# ---------------------------------------------------------------------------
# list_work_dir
# ---------------------------------------------------------------------------


def test_list_work_dir_logs_files(tmp_path, settings):
    """list_work_dir() iterates over files and directories in APP_WORK_DIR."""
    settings.APP_WORK_DIR = str(tmp_path)
    (tmp_path / "file.txt").write_text("content")
    (tmp_path / "subdir").mkdir()

    list_work_dir()  # must not raise — logging is the side effect


# ---------------------------------------------------------------------------
# list_workspace_dir
# ---------------------------------------------------------------------------


def test_list_workspace_dir_logs_files(tmp_path, settings):
    """list_workspace_dir() walks the workspace directory and logs each file."""
    settings.APP_WORK_DIR = str(tmp_path)
    ws = MagicMock(spec=Workspace)
    ws.id = "ws-log"
    workspace_dir = tmp_path / "workspace_ws-log"
    workspace_dir.mkdir()
    (workspace_dir / "doc.pdf").write_text("data")

    list_workspace_dir(ws)  # must not raise


# ---------------------------------------------------------------------------
# cleanup_workspace_dir
# ---------------------------------------------------------------------------


def test_cleanup_workspace_dir_deletes_the_local_folder(tmp_path, settings):
    """cleanup_workspace_dir() deletes the workspace's local folder."""
    settings.APP_WORK_DIR = str(tmp_path)
    ws = MagicMock(spec=Workspace)
    ws.id = "ws-clean"
    workspace_dir = tmp_path / "workspace_ws-clean"
    workspace_dir.mkdir()

    cleanup_workspace_dir(ws)

    assert not workspace_dir.exists()


# ---------------------------------------------------------------------------
# debug_folder
# ---------------------------------------------------------------------------


def test_debug_folder_recurses_into_children():
    """debug_folder() recurses into nested SourceFolders without error."""
    folder = SourceFolder(
        name="root",
        children=[
            SourceFolder(name="child", children=[SourceFolder(name="grandchild")])
        ],
    )

    debug_folder(folder)  # must not raise


# ---------------------------------------------------------------------------
# create_task_result_on_publish
# ---------------------------------------------------------------------------


def test_create_task_result_on_publish_stores_result():
    """create_task_result_on_publish() calls TaskResult.store_result when 'task' is in headers."""
    headers = {
        "task": "core.processing.tasks.export",
        "id": "task-uuid-123",
        "argsrepr": "[]",
        "kwargsrepr": "{}",
    }

    with patch("core.processing.tasks.TaskResult") as mock_task_result_cls:
        create_task_result_on_publish(headers=headers)

    mock_task_result_cls.objects.store_result.assert_called_once()


@pytest.mark.parametrize(
    "task_name",
    ["core.processing.tasks.export_file", "core.processing.tasks.finish_migration"],
)
def test_create_task_result_on_publish_skips_the_tasks_of_a_run(task_name):
    """Only the migration gets a TaskResult, the admin listing one row per
    migration: not each of its files, nor its finish."""
    with patch("core.processing.tasks.TaskResult") as mock_task_result_cls:
        create_task_result_on_publish(
            headers={"task": task_name, "id": "t", "argsrepr": "", "kwargsrepr": ""}
        )

    mock_task_result_cls.objects.store_result.assert_not_called()


def test_create_task_result_on_publish_skips_when_no_task_key():
    """create_task_result_on_publish() returns early when 'task' is not in headers."""
    with patch("core.processing.tasks.TaskResult") as mock_task_result_cls:
        create_task_result_on_publish(headers={"id": "some-id"})

    mock_task_result_cls.objects.store_result.assert_not_called()


# ---------------------------------------------------------------------------
# task_success signal handler
# ---------------------------------------------------------------------------


def test_task_success_handler_saves_workspace_and_cleans_up():
    """task_success() saves the workspace and calls cleanup_workspace_dir."""
    mock_sender = MagicMock()
    mock_sender.name = export.name
    mock_sender.request.id = "task-id-1"

    mock_task_result = MagicMock(spec=TaskResult)
    mock_extra_task = MagicMock(spec=ExtraTaskInfo)
    mock_workspace = MagicMock(spec=Workspace)
    mock_extra_task.workspace = mock_workspace

    with (
        patch("core.processing.tasks.TaskResult") as mock_tr_cls,
        patch("core.processing.tasks.ExtraTaskInfo") as mock_et_cls,
        patch("core.processing.tasks.cleanup_workspace_dir") as mock_cleanup,
    ):
        mock_tr_cls.objects.filter.return_value.first.return_value = mock_task_result
        mock_et_cls.objects.filter.return_value.first.return_value = mock_extra_task

        task_success_handler(sender=mock_sender)

    mock_workspace.save.assert_called_once()
    mock_cleanup.assert_called_once_with(mock_workspace)


# ---------------------------------------------------------------------------
# task_failure signal handler
# ---------------------------------------------------------------------------


def test_task_failure_handler_sets_pending_statuses_to_failure():
    """task_failure() sets PENDING destination statuses to FAILURE."""
    mock_sender = MagicMock()
    mock_sender.name = export.name
    mock_sender.request.id = "task-id-2"

    mock_task_result = MagicMock(spec=TaskResult)
    mock_extra_task = MagicMock(spec=ExtraTaskInfo)
    mock_workspace = MagicMock(spec=Workspace)
    mock_workspace.destination_statuses = {
        "archive": Workspace.Status.PENDING,
        "resana": Workspace.Status.SUCCESS,
    }
    mock_extra_task.workspace = mock_workspace

    with (
        patch("core.processing.tasks.TaskResult") as mock_tr_cls,
        patch("core.processing.tasks.ExtraTaskInfo") as mock_et_cls,
        patch("core.processing.tasks.cleanup_workspace_dir"),
        patch("core.processing.tasks.MailsManager"),
    ):
        mock_tr_cls.objects.filter.return_value.first.return_value = mock_task_result
        mock_et_cls.objects.filter.return_value.first.return_value = mock_extra_task

        task_failure_handler(sender=mock_sender)

    mock_workspace.set_destination_status.assert_called_once_with(
        "archive", Workspace.Status.FAILURE
    )
    mock_workspace.save.assert_called_once()


def test_task_failure_handler_sends_fail_mail():
    """task_failure() sends a failure notification email."""
    mock_sender = MagicMock()
    mock_sender.name = export.name
    mock_sender.request.id = "task-id-3"

    mock_task_result = MagicMock(spec=TaskResult)
    mock_extra_task = MagicMock(spec=ExtraTaskInfo)
    mock_workspace = MagicMock(spec=Workspace)
    mock_workspace.destination_statuses = {}
    mock_extra_task.workspace = mock_workspace

    with (
        patch("core.processing.tasks.TaskResult") as mock_tr_cls,
        patch("core.processing.tasks.ExtraTaskInfo") as mock_et_cls,
        patch("core.processing.tasks.cleanup_workspace_dir"),
        patch("core.processing.tasks.MailsManager") as mock_mails_cls,
    ):
        mock_tr_cls.objects.filter.return_value.first.return_value = mock_task_result
        mock_et_cls.objects.filter.return_value.first.return_value = mock_extra_task

        task_failure_handler(sender=mock_sender)

    mock_mails_cls.return_value.send_fail_mail.assert_called_once_with(
        mock_extra_task.user, mock_workspace, source_token_expired=False
    )


def test_task_failure_handler_asks_to_log_in_again_on_expired_source_token():
    """A run that failed on an expired source token tells the user to log in
    to the source again before relaunching."""
    mock_sender = MagicMock()
    mock_sender.name = export.name
    mock_sender.request.id = "task-id-4"
    mock_extra_task = MagicMock(spec=ExtraTaskInfo)
    mock_workspace = MagicMock(spec=Workspace)
    mock_workspace.destination_statuses = {}
    mock_extra_task.workspace = mock_workspace

    with (
        patch("core.processing.tasks.TaskResult") as mock_tr_cls,
        patch("core.processing.tasks.ExtraTaskInfo") as mock_et_cls,
        patch("core.processing.tasks.cleanup_workspace_dir"),
        patch("core.processing.tasks.MailsManager") as mock_mails_cls,
    ):
        mock_tr_cls.objects.filter.return_value.first.return_value = MagicMock(
            spec=TaskResult
        )
        mock_et_cls.objects.filter.return_value.first.return_value = mock_extra_task

        task_failure_handler(
            sender=mock_sender,
            exception=ResanaTokenExpired("Resana offline token is no longer valid."),
        )

    mock_mails_cls.return_value.send_fail_mail.assert_called_once_with(
        mock_extra_task.user, mock_workspace, source_token_expired=True
    )


# ---------------------------------------------------------------------------
# migration_finished analytics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "handler,status",
    [(task_success_handler, "success"), (task_failure_handler, "failure")],
)
def test_task_handlers_capture_migration_finished(handler, status):
    """Both end-of-task handlers emit migration_finished with the final status."""
    mock_sender = MagicMock()
    mock_sender.name = export.name
    mock_sender.request.id = "task-id-4"

    mock_task_result = MagicMock(spec=TaskResult)
    mock_task_result.date_created = timezone.now() - timedelta(seconds=42)
    mock_extra_task = MagicMock(spec=ExtraTaskInfo)
    mock_extra_task.task_result = mock_task_result
    mock_workspace = MagicMock(spec=Workspace)
    mock_workspace.destination_statuses = {}
    mock_workspace.is_truncated = True
    mock_workspace.download_errors = [{"name": "a"}, {"name": "b"}]
    mock_workspace.upload_errors = [{"path": "c"}]
    mock_extra_task.workspace = mock_workspace
    mock_extra_task.integrity_check_passed = False
    mock_extra_task.files_stats = {"workspace_size_bytes": 3}
    mock_extra_task.integrity_report = {
        "summary": {
            "source_files_count": 7,
            "migrated_files_count": 5,
            "stages": {"ok": 5, "analysis_unfinished": 2},
        }
    }

    with (
        patch("core.processing.tasks.TaskResult") as mock_tr_cls,
        patch("core.processing.tasks.ExtraTaskInfo") as mock_et_cls,
        patch("core.processing.tasks.cleanup_workspace_dir"),
        patch("core.processing.tasks.MailsManager"),
        patch("core.processing.tasks.workspaces_counts", return_value={"c": 1}),
        patch("core.processing.tasks.posthog_capture") as capture,
    ):
        mock_tr_cls.objects.filter.return_value.first.return_value = mock_task_result
        mock_et_cls.objects.filter.return_value.first.return_value = mock_extra_task

        handler(sender=mock_sender)

    capture.assert_called_once()
    event, user, properties = capture.call_args.args
    assert event == "migration_finished"
    assert user is mock_extra_task.user
    assert capture.call_args.kwargs == {"workspace": mock_workspace}
    assert properties["status"] == status
    assert properties["is_truncated"] is True
    assert properties["download_errors_count"] == 2
    assert properties["upload_errors_count"] == 1
    assert properties["integrity_check_passed"] is False
    assert properties["migrated_files_count"] == 5
    assert properties["source_files_count"] == 7
    assert properties["analysis_unfinished_count"] == 2
    assert 42 <= properties["duration_seconds"] < 60
    assert properties["$set"] == {"c": 1}
    assert properties["workspace_size_bytes"] == 3


def test_migration_finished_file_counts_are_none_without_integrity_report():
    """Without integrity check, the file counts are sent as None."""
    mock_sender = MagicMock()
    mock_sender.name = export.name
    mock_sender.request.id = "task-id-5"
    mock_task_result = MagicMock(spec=TaskResult)
    mock_task_result.date_created = timezone.now()
    mock_extra_task = MagicMock(spec=ExtraTaskInfo)
    mock_extra_task.task_result = mock_task_result
    mock_extra_task.integrity_report = {}
    mock_workspace = MagicMock(spec=Workspace)
    mock_workspace.download_errors = []
    mock_extra_task.workspace = mock_workspace

    with (
        patch("core.processing.tasks.TaskResult") as mock_tr_cls,
        patch("core.processing.tasks.ExtraTaskInfo") as mock_et_cls,
        patch("core.processing.tasks.cleanup_workspace_dir"),
        patch("core.processing.tasks.workspaces_counts", return_value={}),
        patch("core.processing.tasks.posthog_capture") as capture,
    ):
        mock_tr_cls.objects.filter.return_value.first.return_value = mock_task_result
        mock_et_cls.objects.filter.return_value.first.return_value = mock_extra_task

        task_success_handler(sender=mock_sender)

    properties = capture.call_args.args[2]
    assert properties["migrated_files_count"] is None
    assert properties["source_files_count"] is None
    assert properties["analysis_unfinished_count"] is None


def test_migration_finished_counts_zero_unfinished_analyses_in_a_report():
    """A report without file under analysis sends 0, not None."""
    mock_sender = MagicMock()
    mock_sender.name = export.name
    mock_sender.request.id = "task-id-6"
    mock_task_result = MagicMock(spec=TaskResult)
    mock_task_result.date_created = timezone.now()
    mock_extra_task = MagicMock(spec=ExtraTaskInfo)
    mock_extra_task.task_result = mock_task_result
    mock_extra_task.integrity_report = {
        "summary": {
            "source_files_count": 1,
            "migrated_files_count": 1,
            "stages": {"ok": 1},
        }
    }
    mock_workspace = MagicMock(spec=Workspace)
    mock_workspace.download_errors = []
    mock_extra_task.workspace = mock_workspace

    with (
        patch("core.processing.tasks.TaskResult") as mock_tr_cls,
        patch("core.processing.tasks.ExtraTaskInfo") as mock_et_cls,
        patch("core.processing.tasks.cleanup_workspace_dir"),
        patch("core.processing.tasks.workspaces_counts", return_value={}),
        patch("core.processing.tasks.posthog_capture") as capture,
    ):
        mock_tr_cls.objects.filter.return_value.first.return_value = mock_task_result
        mock_et_cls.objects.filter.return_value.first.return_value = mock_extra_task

        task_success_handler(sender=mock_sender)

    assert capture.call_args.args[2]["analysis_unfinished_count"] == 0


@pytest.mark.parametrize("handler", [task_success_handler, task_failure_handler])
def test_task_handlers_ignore_the_tasks_of_a_run(handler):
    """finish_migration reports the outcome of a run: the end of its file
    tasks, or of itself, reports nothing."""
    mock_sender = MagicMock()
    mock_sender.name = "core.processing.tasks.export_file"

    with patch("core.processing.tasks._extra_task_of") as extra_task_of:
        handler(sender=mock_sender)

    extra_task_of.assert_not_called()
