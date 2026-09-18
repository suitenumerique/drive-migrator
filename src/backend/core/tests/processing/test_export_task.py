"""Tests for the generic export Celery task."""

# pylint: disable=redefined-outer-name  # pytest fixtures intentionally shadow outer names

from datetime import timedelta
from unittest.mock import ANY, MagicMock, patch

from django.utils import timezone

import pytest
from django_celery_results.models import TaskResult

from core.backends.source import SourceFolder
from core.models import ExtraTaskInfo, User, Workspace
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


@pytest.fixture(autouse=True)
def work_directory(settings, tmp_path):
    """Keep export task filesystem operations inside a temporary directory."""
    settings.APP_WORK_DIR = str(tmp_path)


@pytest.fixture()
def workspace():
    ws = MagicMock(spec=Workspace)
    ws.id = "ws-1"
    ws.title = "My Workspace"
    ws.destination_statuses = {}
    ws.get_destination_status.return_value = Workspace.Status.PENDING
    ws.is_file_integrity_tracked = False
    return ws


@pytest.fixture()
def user():
    u = MagicMock(spec=User)
    u.id = "user-1"
    return u


def _run_export(workspace, user, dest_backends=None, source_folder=None):
    """Helper: run the export task with standard mocks in place."""
    if dest_backends is None:
        dest_backends = []
    if source_folder is None:
        source_folder = SourceFolder(name="root")

    with (
        patch("core.models.Workspace.objects.get", return_value=workspace),
        patch("core.models.User.objects.get", return_value=user),
        patch("core.processing.tasks.SourceManager") as mock_source_manager_cls,
        patch("core.processing.tasks.FolderCreator") as mock_folder_creator_cls,
        patch("core.processing.tasks.DestinationRegistry") as mock_dest_registry,
    ):
        mock_source_backend = (
            mock_source_manager_cls.return_value.get_backend.return_value
        )
        mock_source_backend.get_workspace_structure.return_value = source_folder
        mock_folder_creator_cls.return_value.create_folder.return_value = "/tmp/ws-1"
        mock_dest_registry.get_all.return_value = dest_backends

        export({"workspace": {"id": "ws-1"}, "user": {"id": "user-1"}})  # pylint: disable=no-value-for-parameter

        return (
            mock_source_backend,
            mock_folder_creator_cls.return_value,
            mock_dest_registry,
        )


def test_export_fetches_workspace_and_user(workspace, user):
    """export() loads Workspace and User from the database."""
    with (
        patch(
            "core.models.Workspace.objects.get", return_value=workspace
        ) as mock_ws_get,
        patch("core.models.User.objects.get", return_value=user) as mock_user_get,
        patch("core.processing.tasks.SourceManager") as mock_sm,
        patch("core.processing.tasks.FolderCreator") as mock_fc,
        patch("core.processing.tasks.DestinationRegistry") as mock_dr,
    ):
        mock_sm.return_value.get_backend.return_value.get_workspace_structure.return_value = SourceFolder(
            name="root"
        )
        mock_fc.return_value.create_folder.return_value = "/tmp/ws-1"
        mock_dr.get_all.return_value = []

        export({"workspace": {"id": "ws-1"}, "user": {"id": "user-1"}})  # pylint: disable=no-value-for-parameter

    mock_ws_get.assert_called_once_with(id="ws-1")
    mock_user_get.assert_called_once_with(id="user-1")


def test_export_calls_get_workspace_structure(workspace, user):
    """export() calls source_backend.get_workspace_structure() to get the folder tree."""
    source_backend, _, _ = _run_export(workspace, user)
    source_backend.get_workspace_structure.assert_called_once_with(workspace)


def test_export_skips_file_truncation_when_limit_is_zero(workspace, user, settings):
    """export() does not touch the folder tree when the limit is 0."""
    settings.MIGRATION_FILE_LIMIT_PER_WORKSPACE = 0
    folder_tree = SourceFolder(name="root", files=[MagicMock()])

    _run_export(workspace, user, source_folder=folder_tree)

    assert len(folder_tree.files) == 1


def test_export_truncates_files_when_limit_is_set_and_exceeded(
    workspace, user, settings
):
    """export() truncates the folder tree and marks the workspace as is_truncated."""
    settings.MIGRATION_FILE_LIMIT_PER_WORKSPACE = 1
    folder_tree = SourceFolder(name="root", files=[MagicMock(), MagicMock()])

    _run_export(workspace, user, source_folder=folder_tree)

    assert workspace.is_truncated is True
    assert len(folder_tree.files) == 1
    workspace.save.assert_called()


def test_export_does_not_flag_workspace_when_limit_is_set_but_not_exceeded(
    workspace, user, settings
):
    """export() does not mark the workspace as is_truncated when it's under the limit."""
    settings.MIGRATION_FILE_LIMIT_PER_WORKSPACE = 5
    folder_tree = SourceFolder(name="root", files=[MagicMock()])

    _run_export(workspace, user, source_folder=folder_tree)

    assert workspace.is_truncated is False


def test_export_builds_local_folder(workspace, user):
    """export() calls FolderCreator.create_folder() with workspace, folder tree, and source backend."""
    folder_tree = SourceFolder(name="root")
    source_backend, creator, _ = _run_export(workspace, user, source_folder=folder_tree)
    creator.create_folder.assert_called_once_with(
        workspace, folder_tree, source_backend
    )


def test_export_calls_prepare_export(workspace, user):
    """export() calls source_backend.prepare_export() with the local folder path."""
    source_backend, _, _ = _run_export(workspace, user)
    source_backend.prepare_export.assert_called_once_with(workspace, "/tmp/ws-1")


def test_export_persists_download_errors_on_workspace(workspace, user):
    """export() persists FolderCreator.failed_files onto the workspace's download_errors."""
    failed_files = [{"name": "broken.docx", "error": "403 Forbidden"}]
    with (
        patch("core.models.Workspace.objects.get", return_value=workspace),
        patch("core.models.User.objects.get", return_value=user),
        patch("core.processing.tasks.SourceManager") as mock_sm,
        patch("core.processing.tasks.FolderCreator") as mock_fc,
        patch("core.processing.tasks.DestinationRegistry") as mock_dr,
    ):
        mock_sm.return_value.get_backend.return_value.get_workspace_structure.return_value = SourceFolder(
            name="root"
        )
        mock_fc.return_value.create_folder.return_value = "/tmp/ws-1"
        mock_fc.return_value.failed_files = failed_files
        mock_dr.get_all.return_value = []

        export({"workspace": {"id": "ws-1"}, "user": {"id": "user-1"}})  # pylint: disable=no-value-for-parameter

    assert workspace.download_errors == failed_files
    workspace.save.assert_called()


def test_export_does_not_save_download_errors_when_all_files_succeed(
    workspace, user, settings
):
    """export() does not touch/save download_errors when no file failed."""
    settings.MIGRATION_FILE_LIMIT_PER_WORKSPACE = 0
    with (
        patch("core.models.Workspace.objects.get", return_value=workspace),
        patch("core.models.User.objects.get", return_value=user),
        patch("core.processing.tasks.SourceManager") as mock_sm,
        patch("core.processing.tasks.FolderCreator") as mock_fc,
        patch("core.processing.tasks.DestinationRegistry") as mock_dr,
    ):
        mock_sm.return_value.get_backend.return_value.get_workspace_structure.return_value = SourceFolder(
            name="root"
        )
        mock_fc.return_value.create_folder.return_value = "/tmp/ws-1"
        mock_fc.return_value.failed_files = []
        mock_fc.return_value.files_count = 1
        mock_fc.return_value.files_success = 1
        mock_dr.get_all.return_value = []

        export({"workspace": {"id": "ws-1"}, "user": {"id": "user-1"}})  # pylint: disable=no-value-for-parameter

    workspace.save.assert_not_called()


def test_export_saves_download_errors_with_update_fields(workspace, user, settings):
    """export() saves download_errors with update_fields=["download_errors"] only."""
    settings.MIGRATION_FILE_LIMIT_PER_WORKSPACE = 0
    failed_files = [{"name": "broken.docx", "error": "403 Forbidden"}]
    with (
        patch("core.models.Workspace.objects.get", return_value=workspace),
        patch("core.models.User.objects.get", return_value=user),
        patch("core.processing.tasks.SourceManager") as mock_sm,
        patch("core.processing.tasks.FolderCreator") as mock_fc,
        patch("core.processing.tasks.DestinationRegistry") as mock_dr,
    ):
        mock_sm.return_value.get_backend.return_value.get_workspace_structure.return_value = SourceFolder(
            name="root"
        )
        mock_fc.return_value.create_folder.return_value = "/tmp/ws-1"
        mock_fc.return_value.failed_files = failed_files
        mock_fc.return_value.files_count = 2
        mock_fc.return_value.files_success = 1
        mock_dr.get_all.return_value = []

        export({"workspace": {"id": "ws-1"}, "user": {"id": "user-1"}})  # pylint: disable=no-value-for-parameter

    workspace.save.assert_called_once_with(update_fields=["download_errors"])


def test_export_fails_when_all_downloads_fail(workspace, user):
    """export() raises instead of returning True when every download failed,
    and no destination export runs."""
    failed_files = [
        {"name": "a.docx", "error": "boom"},
        {"name": "b.docx", "error": "boom"},
    ]
    dest_backend = MagicMock()
    dest_backend.name = "archive"

    with (
        patch("core.models.Workspace.objects.get", return_value=workspace),
        patch("core.models.User.objects.get", return_value=user),
        patch("core.processing.tasks.SourceManager") as mock_sm,
        patch("core.processing.tasks.FolderCreator") as mock_fc,
        patch("core.processing.tasks.DestinationRegistry") as mock_dr,
    ):
        mock_sm.return_value.get_backend.return_value.get_workspace_structure.return_value = SourceFolder(
            name="root"
        )
        mock_fc.return_value.create_folder.return_value = "/tmp/ws-1"
        mock_fc.return_value.failed_files = failed_files
        mock_fc.return_value.files_count = 2
        mock_fc.return_value.files_success = 0
        mock_dr.get_all.return_value = [dest_backend]

        with pytest.raises(RuntimeError):
            export({"workspace": {"id": "ws-1"}, "user": {"id": "user-1"}})  # pylint: disable=no-value-for-parameter

    dest_backend.export.assert_not_called()


def test_export_calls_each_pending_destination(workspace, user):
    """export() calls export() on each destination backend whose status is PENDING."""
    workspace.get_destination_status.return_value = Workspace.Status.PENDING

    dest_archive = MagicMock()
    dest_archive.name = "archive"
    dest_resana = MagicMock()
    dest_resana.name = "resana"

    _run_export(workspace, user, dest_backends=[dest_archive, dest_resana])

    dest_archive.export.assert_called_once_with(workspace, user, "/tmp/ws-1")
    dest_resana.export.assert_called_once_with(workspace, user, "/tmp/ws-1")


def test_export_skips_non_pending_destinations(workspace, user):
    """export() skips destinations whose status is not PENDING."""
    dest_archive = MagicMock()
    dest_archive.name = "archive"
    dest_resana = MagicMock()
    dest_resana.name = "resana"

    def get_status(name):
        if name == "archive":
            return Workspace.Status.SUCCESS
        return Workspace.Status.PENDING

    workspace.get_destination_status.side_effect = get_status

    _run_export(workspace, user, dest_backends=[dest_archive, dest_resana])

    dest_archive.export.assert_not_called()
    dest_resana.export.assert_called_once()


# ---------------------------------------------------------------------------
# Integrity tracking
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_tracker_cls():
    with patch("core.processing.tasks.IntegrityTracker") as tracker_cls:
        yield tracker_cls


def _run_export_with_tracker(workspace, user, dest_backends, setup_creator=None):
    """Run export() with standard mocks; return (creator, source folder)."""
    folder = SourceFolder(name="root")
    with (
        patch("core.models.Workspace.objects.get", return_value=workspace),
        patch("core.models.User.objects.get", return_value=user),
        patch("core.processing.tasks.SourceManager") as mock_sm,
        patch("core.processing.tasks.FolderCreator") as mock_fc,
        patch("core.processing.tasks.DestinationRegistry") as mock_dr,
    ):
        mock_sm.return_value.get_backend.return_value.get_workspace_structure.return_value = folder
        creator = mock_fc.return_value
        creator.create_folder.return_value = "/tmp/ws-1"
        creator.failed_files = []
        if setup_creator:
            setup_creator(creator)
        mock_dr.get_all.return_value = dest_backends

        export({"workspace": {"id": "ws-1"}, "user": {"id": "user-1"}})  # pylint: disable=no-value-for-parameter
    return creator, folder


def _destination(name):
    destination = MagicMock()
    destination.name = name
    return destination


def test_export_feeds_integrity_tracker_and_saves_it(workspace, user, mock_tracker_cls):
    """export() gives the tracker the kept files, the retrieval and the pending
    destinations, then saves the report of a successful run."""
    dest_archive = _destination("archive")
    dest_drive = _destination("drive")
    workspace.get_destination_status.side_effect = lambda name: (
        Workspace.Status.SUCCESS if name == "archive" else Workspace.Status.PENDING
    )

    creator, folder = _run_export_with_tracker(
        workspace, user, [dest_archive, dest_drive]
    )

    mock_tracker_cls.assert_called_once_with(workspace, folder)
    tracker = mock_tracker_cls.return_value
    tracker.set_kept_files.assert_called_once_with(folder)
    tracker.set_retrieval.assert_called_once_with(
        creator, creator.get_workspace_path.return_value
    )
    tracker.add_exported_destination.assert_called_once_with(dest_drive)
    tracker.save.assert_called_once_with(ANY, run_failed=False)


def test_export_saves_integrity_report_when_a_destination_fails(
    workspace, user, mock_tracker_cls
):
    """A failing destination is still checked and the original error propagates."""
    dest_drive = _destination("drive")
    dest_drive.export.side_effect = RuntimeError("Drive down")

    with pytest.raises(RuntimeError, match="Drive down"):
        _run_export_with_tracker(workspace, user, [dest_drive])

    tracker = mock_tracker_cls.return_value
    tracker.add_exported_destination.assert_called_once_with(dest_drive)
    tracker.save.assert_called_once_with(ANY, run_failed=True)


def test_export_saves_integrity_report_when_all_downloads_fail(
    workspace, user, mock_tracker_cls
):
    """The report is also saved when the run stops during retrieval."""

    def all_downloads_failed(creator):
        creator.failed_files = [{"id": "f1", "name": "a", "path": "a", "error": "x"}]
        creator.files_count = 1
        creator.files_success = 0

    with pytest.raises(RuntimeError, match="failed to download"):
        _run_export_with_tracker(workspace, user, [], all_downloads_failed)

    mock_tracker_cls.return_value.save.assert_called_once_with(ANY, run_failed=True)


# ---------------------------------------------------------------------------
# begin_export() / finalize_export() hooks (#215)
# ---------------------------------------------------------------------------


def test_export_calls_begin_export_before_get_workspace_structure(workspace, user):
    """begin_export() must run before any file is read from the source."""
    calls = []
    with (
        patch("core.models.Workspace.objects.get", return_value=workspace),
        patch("core.models.User.objects.get", return_value=user),
        patch("core.processing.tasks.SourceManager") as mock_sm,
        patch("core.processing.tasks.FolderCreator") as mock_fc,
        patch("core.processing.tasks.DestinationRegistry") as mock_dr,
    ):

        def get_workspace_structure(_ws):
            calls.append("structure")
            return SourceFolder(name="root")

        source_backend = mock_sm.return_value.get_backend.return_value
        source_backend.begin_export.side_effect = lambda ws: calls.append("begin")
        source_backend.get_workspace_structure.side_effect = get_workspace_structure
        mock_fc.return_value.create_folder.return_value = "/tmp/ws-1"
        mock_dr.get_all.return_value = []

        export({"workspace": {"id": "ws-1"}, "user": {"id": "user-1"}})  # pylint: disable=no-value-for-parameter

    assert calls == ["begin", "structure"]


def test_export_calls_finalize_export_after_destinations(workspace, user):
    """finalize_export() must run after every destination has been processed."""
    source_backend, _, _ = _run_export(workspace, user)
    source_backend.finalize_export.assert_called_once_with(workspace)


def test_export_calls_finalize_export_even_when_get_workspace_structure_raises(
    workspace, user
):
    """finalize_export() must still run if the migration body raises partway through."""
    with (
        patch("core.models.Workspace.objects.get", return_value=workspace),
        patch("core.models.User.objects.get", return_value=user),
        patch("core.processing.tasks.SourceManager") as mock_sm,
        patch("core.processing.tasks.FolderCreator"),
        patch("core.processing.tasks.DestinationRegistry"),
    ):
        source_backend = mock_sm.return_value.get_backend.return_value
        source_backend.get_workspace_structure.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            export({"workspace": {"id": "ws-1"}, "user": {"id": "user-1"}})  # pylint: disable=no-value-for-parameter

    source_backend.finalize_export.assert_called_once_with(workspace)


def test_export_calls_finalize_export_even_when_begin_export_raises(workspace, user):
    """A partial begin_export() (e.g. locked, then a grant fails) must still be reversed."""
    with (
        patch("core.models.Workspace.objects.get", return_value=workspace),
        patch("core.models.User.objects.get", return_value=user),
        patch("core.processing.tasks.SourceManager") as mock_sm,
        patch("core.processing.tasks.FolderCreator"),
        patch("core.processing.tasks.DestinationRegistry"),
    ):
        source_backend = mock_sm.return_value.get_backend.return_value
        source_backend.begin_export.side_effect = RuntimeError("grant failed")

        with pytest.raises(RuntimeError, match="grant failed"):
            export({"workspace": {"id": "ws-1"}, "user": {"id": "user-1"}})  # pylint: disable=no-value-for-parameter

    source_backend.get_workspace_structure.assert_not_called()
    source_backend.finalize_export.assert_called_once_with(workspace)


def test_export_calls_begin_export_with_workspace(workspace, user):
    """begin_export() receives the workspace being migrated."""
    source_backend, _, _ = _run_export(workspace, user)
    source_backend.begin_export.assert_called_once_with(workspace)


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


def test_cleanup_workspace_dir_deletes_folder_and_archive(tmp_path, settings):
    """cleanup_workspace_dir() deletes the local folder and the S3 archive."""
    settings.APP_WORK_DIR = str(tmp_path)
    ws = MagicMock(spec=Workspace)
    ws.id = "ws-clean"
    workspace_dir = tmp_path / "workspace_ws-clean"
    workspace_dir.mkdir()

    with patch("core.processing.tasks.ArchiveManager") as mock_archive_cls:
        cleanup_workspace_dir(ws)

    assert not workspace_dir.exists()
    mock_archive_cls.return_value.delete_archive.assert_called_once_with(ws)


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
        mock_extra_task.user, mock_workspace
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
    mock_sender.request.id = "task-id-4"

    mock_task_result = MagicMock(spec=TaskResult)
    mock_task_result.date_created = timezone.now() - timedelta(seconds=42)
    mock_extra_task = MagicMock(spec=ExtraTaskInfo)
    mock_extra_task.task_result = mock_task_result
    mock_workspace = MagicMock(spec=Workspace)
    mock_workspace.destination_statuses = {}
    mock_workspace.is_truncated = True
    mock_workspace.download_errors = [{"name": "a"}, {"name": "b"}]
    mock_extra_task.workspace = mock_workspace
    mock_extra_task.integrity_check_passed = False
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
    assert properties["integrity_check_passed"] is False
    assert properties["migrated_files_count"] == 5
    assert properties["source_files_count"] == 7
    assert properties["analysis_unfinished_count"] == 2
    assert 42 <= properties["duration_seconds"] < 60
    assert properties["$set"] == {"c": 1}


def test_migration_finished_file_counts_are_none_without_integrity_report():
    """Without integrity check, the file counts are sent as None."""
    mock_sender = MagicMock()
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
