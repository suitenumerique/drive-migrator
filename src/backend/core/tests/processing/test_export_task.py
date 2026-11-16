"""Tests for the generic export Celery task."""

# pylint: disable=redefined-outer-name  # pytest fixtures intentionally shadow outer names
# pylint: disable=no-member  # django.core.mail.outbox is injected by the test runner

import os
import uuid
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import ANY, MagicMock, patch

from django.conf import settings
from django.core import mail
from django.utils import timezone

import pytest
from django_celery_results.models import TaskResult

from core import factories
from core.backends.source import SourceFile, SourceFolder
from core.models import ExtraTaskInfo, User, Workspace
from core.processing.folder_creator import FolderCreator
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

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def work_directory(settings, tmp_path):
    """Keep export task filesystem operations inside a temporary directory."""
    settings.APP_WORK_DIR = str(tmp_path)


@pytest.fixture()
def user():
    return factories.UserFactory()


@pytest.fixture()
def workspace(user):
    """A real workspace linked to the user, with integrity tracking off."""
    created = factories.WorkspaceFactory(
        title="My Workspace",
        is_file_integrity_tracked=False,
    )
    user.workspaces.add(created)
    return created


def _payload(workspace, user):
    return {
        "workspace": {"id": str(workspace.id)},
        "user": {"id": str(user.id)},
    }


def _workspace_dir(workspace):
    return f"{settings.APP_WORK_DIR}/workspace_{workspace.id}"


def _file(file_id, name, extension=".txt"):
    return SourceFile(id=file_id, name=name, extension=extension, download_url="")


def _write_downloaded_file(_file, destination):
    """Stand in for a source download that succeeds and writes the file."""
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    with open(destination, "wb"):
        pass


def _set_statuses(workspace, **statuses):
    for name, status in statuses.items():
        workspace.set_destination_status(name, status)
    workspace.save()


def _run_task(task_id, workspace, user, **fields):
    task_result = TaskResult.objects.create(task_id=task_id)
    return ExtraTaskInfo.objects.create(
        task_result=task_result,
        workspace=workspace,
        user=user,
        **fields,
    )


def _sender(task_id):
    sender = MagicMock()
    sender.request.id = task_id
    return sender


@contextmanager
def _patched_boundaries(dest_backends=None, source_folder=None):
    """Patch the source and the destination registry.

    Folder creation runs for real, under the temporary work directory.
    ``download_file`` writes an empty file unless the test replaces it.
    """
    if dest_backends is None:
        dest_backends = []
    if source_folder is None:
        source_folder = SourceFolder(name="root")

    with (
        patch("core.processing.tasks.SourceManager") as source_manager_cls,
        patch("core.processing.tasks.DestinationRegistry") as destination_registry,
    ):
        source_backend = source_manager_cls.return_value.get_backend.return_value
        source_backend.get_workspace_structure.return_value = source_folder
        source_backend.download_file.side_effect = _write_downloaded_file
        destination_registry.get_all.return_value = dest_backends
        yield source_backend


def _run_export(workspace, user, dest_backends=None, source_folder=None):
    """Run export() against the database, with the external boundaries patched."""
    with _patched_boundaries(dest_backends, source_folder) as source_backend:
        export(_payload(workspace, user))  # pylint: disable=no-value-for-parameter
    return source_backend


def _assert_exported(destination, workspace, user):
    destination.export.assert_called_once()
    exported_workspace, exported_user, path = destination.export.call_args.args
    assert exported_workspace.pk == workspace.pk
    assert exported_user.pk == user.pk
    assert path == _workspace_dir(workspace)


def test_export_raises_when_the_workspace_does_not_exist(user):
    """export() loads the workspace from the database."""
    missing = {"workspace": {"id": str(uuid.uuid4())}, "user": {"id": str(user.id)}}
    with pytest.raises(Workspace.DoesNotExist):
        export(missing)  # pylint: disable=no-value-for-parameter


def test_export_raises_when_the_user_does_not_exist(workspace):
    """export() loads the user from the database."""
    missing = {
        "workspace": {"id": str(workspace.id)},
        "user": {"id": str(uuid.uuid4())},
    }
    with pytest.raises(User.DoesNotExist):
        export(missing)  # pylint: disable=no-value-for-parameter


def test_export_calls_get_workspace_structure(workspace, user):
    """export() calls source_backend.get_workspace_structure() with the loaded workspace."""
    source_backend = _run_export(workspace, user)
    source_backend.get_workspace_structure.assert_called_once()
    loaded = source_backend.get_workspace_structure.call_args.args[0]
    assert loaded.pk == workspace.pk


def test_export_skips_file_truncation_when_limit_is_zero(workspace, user, settings):
    """export() does not touch the folder tree when the limit is 0."""
    settings.MIGRATION_FILE_LIMIT_PER_WORKSPACE = 0
    folder_tree = SourceFolder(name="root", files=[_file("f1", "kept")])

    _run_export(workspace, user, source_folder=folder_tree)

    assert len(folder_tree.files) == 1
    workspace.refresh_from_db()
    assert workspace.is_truncated is False


def test_export_truncates_files_when_limit_is_set_and_exceeded(
    workspace, user, settings
):
    """export() truncates the folder tree and marks the workspace as is_truncated."""
    settings.MIGRATION_FILE_LIMIT_PER_WORKSPACE = 1
    folder_tree = SourceFolder(
        name="root", files=[_file("f1", "kept"), _file("f2", "dropped")]
    )

    _run_export(workspace, user, source_folder=folder_tree)

    assert len(folder_tree.files) == 1
    workspace.refresh_from_db()
    assert workspace.is_truncated is True


def test_export_does_not_flag_workspace_when_limit_is_set_but_not_exceeded(
    workspace, user, settings
):
    """export() does not mark the workspace as is_truncated when it's under the limit."""
    settings.MIGRATION_FILE_LIMIT_PER_WORKSPACE = 5
    folder_tree = SourceFolder(name="root", files=[_file("f1", "only")])

    _run_export(workspace, user, source_folder=folder_tree)

    workspace.refresh_from_db()
    assert workspace.is_truncated is False


def test_export_builds_local_folder(workspace, user):
    """export() downloads the tree into the workspace directory and prepares it."""
    folder_tree = SourceFolder(name="root", files=[_file("f1", "doc")])
    source_backend = _run_export(workspace, user, source_folder=folder_tree)

    assert os.path.isdir(_workspace_dir(workspace))
    source_backend.download_file.assert_called_once()
    source_backend.prepare_export.assert_called_once()
    loaded, path = source_backend.prepare_export.call_args.args
    assert loaded.pk == workspace.pk
    assert path == _workspace_dir(workspace)


def test_export_calls_prepare_export(workspace, user):
    """export() calls source_backend.prepare_export() with the local folder path."""
    source_backend = _run_export(workspace, user)
    source_backend.prepare_export.assert_called_once()
    loaded, path = source_backend.prepare_export.call_args.args
    assert loaded.pk == workspace.pk
    assert path == _workspace_dir(workspace)


def test_export_persists_download_errors_on_workspace(workspace, user):
    """export() stores the files FolderCreator could not download."""
    workspace.download_errors = [
        {"id": "old", "name": "old.txt", "path": "old.txt", "error": "previous"}
    ]
    workspace.save(update_fields=["download_errors"])
    folder_tree = SourceFolder(
        name="root",
        files=[_file("bad", "broken", ".docx"), _file("good", "ok")],
    )

    def download(source_file, destination):
        if source_file.id == "bad":
            raise RuntimeError("403 Forbidden")
        _write_downloaded_file(source_file, destination)

    with _patched_boundaries(source_folder=folder_tree) as source_backend:
        source_backend.download_file.side_effect = download
        export(_payload(workspace, user))  # pylint: disable=no-value-for-parameter

    workspace.refresh_from_db()
    assert [error["name"] for error in workspace.download_errors] == ["broken.docx"]
    assert "403 Forbidden" in workspace.download_errors[0]["error"]


def test_export_keeps_earlier_download_errors_when_the_source_token_expires(
    workspace, user
):
    """An expired source token stops the run with that error, so the user is
    asked to log in again, and keeps the files that failed before it."""
    files = [_file(f"f{i}", f"doc{i}", ".pdf") for i in range(3)]
    folder_tree = SourceFolder(name="root", files=files)

    with _patched_boundaries(source_folder=folder_tree) as source_backend:
        source_backend.download_file.side_effect = [
            RuntimeError("504 Gateway Timeout"),
            ResanaTokenExpired("Resana offline token is no longer valid."),
        ]
        with pytest.raises(ResanaTokenExpired):
            export(_payload(workspace, user))  # pylint: disable=no-value-for-parameter

    assert source_backend.download_file.call_count == 2
    workspace.refresh_from_db()
    assert [error["name"] for error in workspace.download_errors] == [
        "doc0.pdf",
        "doc1.pdf",
    ]


def test_export_resets_previous_run_errors_before_starting(workspace, user):
    """export() clears the errors of a previous run before the source is called."""
    workspace.download_errors = [
        {"id": "old", "name": "old.txt", "path": "old.txt", "error": "previous"}
    ]
    workspace.upload_errors = [{"path": "old.txt", "error": "refused"}]
    workspace.save(update_fields=["download_errors", "upload_errors"])

    def check_reset(loaded):
        assert loaded.pk == workspace.pk
        loaded.refresh_from_db()
        assert loaded.download_errors == []
        assert loaded.upload_errors == []
        raise RuntimeError("stop")

    with _patched_boundaries() as source_backend:
        source_backend.begin_export.side_effect = check_reset
        with pytest.raises(RuntimeError, match="stop"):
            export(_payload(workspace, user))  # pylint: disable=no-value-for-parameter


def test_export_clears_previous_run_rejected_files_before_starting(workspace, user):
    """export() deletes the rejected files zips of a previous run before the source
    is called."""
    with patch("core.processing.tasks.RejectedFilesArchive") as archive_cls:

        def check_clear(loaded):
            archive_cls.clear.assert_called_once()
            assert archive_cls.clear.call_args.args[0].pk == workspace.pk
            assert loaded.pk == workspace.pk
            raise RuntimeError("stop")

        with _patched_boundaries() as source_backend:
            source_backend.begin_export.side_effect = check_clear
            with pytest.raises(RuntimeError, match="stop"):
                export(_payload(workspace, user))  # pylint: disable=no-value-for-parameter


def test_export_leaves_no_download_errors_when_all_files_succeed(workspace, user):
    """A fully successful download records no download error."""
    workspace.download_errors = [
        {"id": "old", "name": "old.txt", "path": "old.txt", "error": "previous"}
    ]
    workspace.save(update_fields=["download_errors"])
    folder_tree = SourceFolder(name="root", files=[_file("f1", "ok")])

    _run_export(workspace, user, source_folder=folder_tree)

    workspace.refresh_from_db()
    assert workspace.download_errors == []


def test_export_persists_partial_download_errors(workspace, user, settings):
    """export() keeps the failed files and still finishes when some downloads work."""
    settings.MIGRATION_FILE_LIMIT_PER_WORKSPACE = 0
    folder_tree = SourceFolder(
        name="root",
        files=[_file("bad", "broken", ".docx"), _file("good", "ok")],
    )

    def download(source_file, destination):
        if source_file.id == "bad":
            raise RuntimeError("403 Forbidden")
        _write_downloaded_file(source_file, destination)

    with _patched_boundaries(source_folder=folder_tree) as source_backend:
        source_backend.download_file.side_effect = download
        result = export(_payload(workspace, user))  # pylint: disable=no-value-for-parameter

    assert result is True
    workspace.refresh_from_db()
    assert len(workspace.download_errors) == 1
    assert workspace.download_errors[0]["name"] == "broken.docx"


def test_export_fails_when_all_downloads_fail(workspace, user):
    """export() raises instead of returning True when every download failed,
    and no destination export runs."""
    _set_statuses(workspace, archive=Workspace.Status.PENDING)
    dest_backend = MagicMock()
    dest_backend.name = "archive"
    folder_tree = SourceFolder(
        name="root",
        files=[_file("a", "a", ".docx"), _file("b", "b", ".docx")],
    )

    def fail(_source_file, _destination):
        raise RuntimeError("boom")

    with _patched_boundaries([dest_backend], folder_tree) as source_backend:
        source_backend.download_file.side_effect = fail
        with pytest.raises(RuntimeError, match="failed to download"):
            export(_payload(workspace, user))  # pylint: disable=no-value-for-parameter

    dest_backend.export.assert_not_called()
    workspace.refresh_from_db()
    assert [error["name"] for error in workspace.download_errors] == [
        "a.docx",
        "b.docx",
    ]


def test_export_calls_each_pending_destination(workspace, user):
    """export() calls export() on each destination backend whose status is PENDING."""
    _set_statuses(
        workspace,
        archive=Workspace.Status.PENDING,
        resana=Workspace.Status.PENDING,
    )
    dest_archive = MagicMock()
    dest_archive.name = "archive"
    dest_resana = MagicMock()
    dest_resana.name = "resana"

    _run_export(workspace, user, dest_backends=[dest_archive, dest_resana])

    _assert_exported(dest_archive, workspace, user)
    _assert_exported(dest_resana, workspace, user)


def test_export_skips_non_pending_destinations(workspace, user):
    """export() skips destinations whose status is not PENDING."""
    _set_statuses(
        workspace,
        archive=Workspace.Status.SUCCESS,
        resana=Workspace.Status.PENDING,
    )
    dest_archive = MagicMock()
    dest_archive.name = "archive"
    dest_resana = MagicMock()
    dest_resana.name = "resana"

    _run_export(workspace, user, dest_backends=[dest_archive, dest_resana])

    dest_archive.export.assert_not_called()
    _assert_exported(dest_resana, workspace, user)


# ---------------------------------------------------------------------------
# Integrity tracking
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_tracker_cls():
    with patch("core.processing.tasks.IntegrityTracker") as tracker_cls:
        yield tracker_cls


def _destination(name):
    destination = MagicMock()
    destination.name = name
    return destination


def test_export_feeds_integrity_tracker_and_saves_it(workspace, user, mock_tracker_cls):
    """export() gives the tracker the kept files, the retrieval and the pending
    destinations, then saves the report of a successful run."""
    _set_statuses(
        workspace,
        archive=Workspace.Status.SUCCESS,
        drive=Workspace.Status.PENDING,
    )
    dest_archive = _destination("archive")
    dest_drive = _destination("drive")
    folder = SourceFolder(name="root")

    _run_export(workspace, user, [dest_archive, dest_drive], folder)

    tracked_workspace, tracked_folder = mock_tracker_cls.call_args.args
    assert tracked_workspace.pk == workspace.pk
    assert tracked_folder is folder
    tracker = mock_tracker_cls.return_value
    tracker.set_kept_files.assert_called_once_with(folder)
    retrieval, path = tracker.set_retrieval.call_args.args
    assert isinstance(retrieval, FolderCreator)
    assert path == _workspace_dir(workspace)
    tracker.add_exported_destination.assert_called_once_with(dest_drive)
    tracker.save.assert_called_once_with(ANY, run_failed=False)


def test_export_saves_integrity_report_when_a_destination_fails(
    workspace, user, mock_tracker_cls
):
    """A failing destination is still checked and the original error propagates."""
    _set_statuses(workspace, drive=Workspace.Status.PENDING)
    dest_drive = _destination("drive")
    dest_drive.export.side_effect = RuntimeError("Drive down")

    with pytest.raises(RuntimeError, match="Drive down"):
        _run_export(workspace, user, [dest_drive])

    tracker = mock_tracker_cls.return_value
    tracker.add_exported_destination.assert_called_once_with(dest_drive)
    tracker.save.assert_called_once_with(ANY, run_failed=True)


def test_export_saves_integrity_report_when_all_downloads_fail(
    workspace, user, mock_tracker_cls
):
    """The report is also saved when the run stops during retrieval."""
    folder = SourceFolder(name="root", files=[_file("f1", "a")])

    def fail(_source_file, _destination):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="failed to download"):
        with _patched_boundaries(source_folder=folder) as source_backend:
            source_backend.download_file.side_effect = fail
            export(_payload(workspace, user))  # pylint: disable=no-value-for-parameter

    mock_tracker_cls.return_value.save.assert_called_once_with(ANY, run_failed=True)


# ---------------------------------------------------------------------------
# begin_export() / finalize_export() hooks (#215)
# ---------------------------------------------------------------------------


def test_export_calls_begin_export_before_get_workspace_structure(workspace, user):
    """begin_export() must run before any file is read from the source."""
    calls = []

    def get_workspace_structure(_loaded):
        calls.append("structure")
        return SourceFolder(name="root")

    with _patched_boundaries() as source_backend:
        source_backend.begin_export.side_effect = lambda _loaded: calls.append("begin")
        source_backend.get_workspace_structure.side_effect = get_workspace_structure
        export(_payload(workspace, user))  # pylint: disable=no-value-for-parameter

    assert calls == ["begin", "structure"]


def test_export_calls_finalize_export_after_destinations(workspace, user):
    """finalize_export() must run after every destination has been processed."""
    source_backend = _run_export(workspace, user)
    source_backend.finalize_export.assert_called_once()
    assert source_backend.finalize_export.call_args.args[0].pk == workspace.pk


def test_export_calls_finalize_export_even_when_get_workspace_structure_raises(
    workspace, user
):
    """finalize_export() must still run if the migration body raises partway through."""
    with _patched_boundaries() as source_backend:
        source_backend.get_workspace_structure.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            export(_payload(workspace, user))  # pylint: disable=no-value-for-parameter

    source_backend.finalize_export.assert_called_once()
    assert source_backend.finalize_export.call_args.args[0].pk == workspace.pk


def test_export_calls_finalize_export_even_when_begin_export_raises(workspace, user):
    """A partial begin_export() (e.g. locked, then a grant fails) must still be reversed."""
    with _patched_boundaries() as source_backend:
        source_backend.begin_export.side_effect = RuntimeError("grant failed")

        with pytest.raises(RuntimeError, match="grant failed"):
            export(_payload(workspace, user))  # pylint: disable=no-value-for-parameter

    source_backend.get_workspace_structure.assert_not_called()
    source_backend.finalize_export.assert_called_once()
    assert source_backend.finalize_export.call_args.args[0].pk == workspace.pk


def test_export_calls_begin_export_with_workspace(workspace, user):
    """begin_export() receives the workspace being migrated."""
    source_backend = _run_export(workspace, user)
    source_backend.begin_export.assert_called_once()
    assert source_backend.begin_export.call_args.args[0].pk == workspace.pk


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


def test_list_workspace_dir_logs_files(workspace):
    """list_workspace_dir() walks the workspace directory and logs each file."""
    workspace_dir = _workspace_dir(workspace)
    os.makedirs(workspace_dir)
    with open(os.path.join(workspace_dir, "doc.pdf"), "w", encoding="utf-8") as handle:
        handle.write("data")

    list_workspace_dir(workspace)  # must not raise


# ---------------------------------------------------------------------------
# cleanup_workspace_dir
# ---------------------------------------------------------------------------


def test_cleanup_workspace_dir_deletes_folder_and_archive(workspace):
    """cleanup_workspace_dir() deletes the local folder and the S3 archive."""
    workspace_dir = _workspace_dir(workspace)
    os.makedirs(workspace_dir)

    with patch("core.processing.tasks.ArchiveManager") as mock_archive_cls:
        cleanup_workspace_dir(workspace)

    assert not os.path.exists(workspace_dir)
    deleted = mock_archive_cls.return_value.delete_archive.call_args.args[0]
    assert deleted.pk == workspace.pk


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
    """create_task_result_on_publish() stores a PENDING TaskResult when 'task' is set."""
    headers = {
        "task": "core.processing.tasks.export",
        "id": "task-uuid-123",
        "argsrepr": "[]",
        "kwargsrepr": "{}",
    }

    create_task_result_on_publish(headers=headers)

    stored = TaskResult.objects.get(task_id="task-uuid-123")
    assert stored.task_name == "core.processing.tasks.export"
    assert stored.status == "PENDING"


def test_create_task_result_on_publish_skips_when_no_task_key():
    """create_task_result_on_publish() returns early when 'task' is not in headers."""
    create_task_result_on_publish(headers={"id": "some-id"})

    assert not TaskResult.objects.filter(task_id="some-id").exists()


# ---------------------------------------------------------------------------
# task_success signal handler
# ---------------------------------------------------------------------------


def test_task_success_handler_saves_workspace_and_cleans_up(workspace, user):
    """task_success() saves the workspace and deletes its local folder and archive."""
    extra = _run_task("task-id-1", workspace, user)
    os.makedirs(_workspace_dir(workspace))

    with patch("core.processing.tasks.ArchiveManager") as archive_cls:
        task_success_handler(sender=_sender(extra.task_result.task_id))

    assert not os.path.exists(_workspace_dir(workspace))
    deleted = archive_cls.return_value.delete_archive.call_args.args[0]
    assert deleted.pk == workspace.pk


# ---------------------------------------------------------------------------
# task_failure signal handler
# ---------------------------------------------------------------------------


def test_task_failure_handler_sets_pending_statuses_to_failure(workspace, user):
    """task_failure() sets PENDING destination statuses to FAILURE."""
    _set_statuses(
        workspace,
        archive=Workspace.Status.PENDING,
        resana=Workspace.Status.SUCCESS,
    )
    _run_task("task-id-2", workspace, user)

    with patch("core.processing.tasks.ArchiveManager"):
        task_failure_handler(sender=_sender("task-id-2"))

    workspace.refresh_from_db()
    assert workspace.get_destination_status("archive") == Workspace.Status.FAILURE
    assert workspace.get_destination_status("resana") == Workspace.Status.SUCCESS


def test_task_failure_handler_sends_fail_mail(workspace, user):
    """task_failure() sends a failure notification email."""
    _run_task("task-id-3", workspace, user)

    with patch("core.processing.tasks.ArchiveManager"):
        task_failure_handler(sender=_sender("task-id-3"))

    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == [user.email]
    assert workspace.title in sent.body


def test_task_failure_handler_asks_to_log_in_again_on_expired_source_token(
    workspace, user
):
    """A run that failed on an expired source token tells the user to log in
    to the source again before relaunching."""
    _run_task("task-id-4", workspace, user)

    with patch("core.processing.tasks.ArchiveManager"):
        task_failure_handler(
            sender=_sender("task-id-4"),
            exception=ResanaTokenExpired("Resana offline token is no longer valid."),
        )

    assert len(mail.outbox) == 1
    assert "Reconnectez-vous" in mail.outbox[0].body


# ---------------------------------------------------------------------------
# migration_finished analytics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "handler,status",
    [(task_success_handler, "success"), (task_failure_handler, "failure")],
)
def test_task_handlers_capture_migration_finished(handler, status, workspace, user):
    """Both end-of-task handlers emit migration_finished with the final status."""
    task_result = TaskResult.objects.create(task_id=f"task-finished-{status}")
    task_result.date_created = timezone.now() - timedelta(seconds=42)
    task_result.save(update_fields=["date_created"])
    ExtraTaskInfo.objects.create(
        task_result=task_result,
        workspace=workspace,
        user=user,
        integrity_check_passed=False,
        integrity_report={
            "summary": {
                "source_files_count": 7,
                "migrated_files_count": 5,
                "stages": {"ok": 5, "analysis_unfinished": 2},
            }
        },
    )
    workspace.is_truncated = True
    workspace.download_errors = [
        {"id": "a", "name": "a.txt", "path": "a.txt", "error": "x"},
        {"id": "b", "name": "b.txt", "path": "b.txt", "error": "y"},
    ]
    workspace.upload_errors = [
        {"path": "c.txt", "error": "refused", "destination": "drive"}
    ]
    workspace.save()
    folder = _workspace_dir(workspace)
    os.makedirs(folder)
    with open(os.path.join(folder, "note.txt"), "wb") as handle:
        handle.write(b"abc")

    with (
        patch("core.processing.tasks.ArchiveManager"),
        patch("core.processing.tasks.posthog_capture") as capture,
    ):
        handler(sender=_sender(task_result.task_id))

    capture.assert_called_once()
    event, captured_user, properties = capture.call_args.args
    assert event == "migration_finished"
    assert captured_user.pk == user.pk
    assert capture.call_args.kwargs["workspace"].pk == workspace.pk
    assert properties["status"] == status
    assert properties["is_truncated"] is True
    assert properties["download_errors_count"] == 2
    assert properties["upload_errors_count"] == 1
    assert properties["integrity_check_passed"] is False
    assert properties["migrated_files_count"] == 5
    assert properties["source_files_count"] == 7
    assert properties["analysis_unfinished_count"] == 2
    assert 42 <= properties["duration_seconds"] < 60
    assert properties["$set"] == {
        "workspaces_found": 1,
        "workspaces_not_migrated": 1,
    }
    assert properties["workspace_size_bytes"] == 3


def test_migration_finished_file_counts_are_none_without_integrity_report(
    workspace, user
):
    """Without integrity check, the file counts are sent as None."""
    extra = _run_task("task-id-5", workspace, user)

    with (
        patch("core.processing.tasks.ArchiveManager"),
        patch("core.processing.tasks.posthog_capture") as capture,
    ):
        task_success_handler(sender=_sender(extra.task_result.task_id))

    properties = capture.call_args.args[2]
    assert properties["migrated_files_count"] is None
    assert properties["source_files_count"] is None
    assert properties["analysis_unfinished_count"] is None


def test_migration_finished_counts_zero_unfinished_analyses_in_a_report(
    workspace, user
):
    """A report without file under analysis sends 0, not None."""
    extra = _run_task(
        "task-id-6",
        workspace,
        user,
        integrity_report={
            "summary": {
                "source_files_count": 1,
                "migrated_files_count": 1,
                "stages": {"ok": 1},
            }
        },
    )

    with (
        patch("core.processing.tasks.ArchiveManager"),
        patch("core.processing.tasks.posthog_capture") as capture,
    ):
        task_success_handler(sender=_sender(extra.task_result.task_id))

    assert capture.call_args.args[2]["analysis_unfinished_count"] == 0


def test_export_logs_with_the_workspace(workspace, user, console_logs):
    """The logs of a migration should tell its workspace."""
    with patch("core.processing.tasks.log_token_status"):
        _run_export(workspace, user)

    task_logs = [
        log for log in console_logs if log["logger"] == "core.processing.tasks"
    ]
    assert task_logs[0] == {
        "event": "Starting workspace ws-1 ...",
        "level": "info",
        "logger": "core.processing.tasks",
        "workspace_id": "ws-1",
    }
    assert task_logs[-1] == {
        "event": "Task done",
        "level": "info",
        "logger": "core.processing.tasks",
        "workspace_id": "ws-1",
    }


def test_export_logs_token_status_before_migrating(workspace, user):
    """The tokens of the services the workspace relies on are logged at task start."""
    workspace.source_type = "resana"
    _set_statuses(workspace, drive=Workspace.Status.PENDING)

    with (
        _patched_boundaries(),
        patch("core.processing.tasks.log_token_status") as mock_log,
    ):
        export(_payload(workspace, user))  # pylint: disable=no-value-for-parameter

    _logger, message, logged_user = mock_log.call_args.args
    assert message == f"Before migrating workspace {workspace.id}"
    assert logged_user.pk == user.pk
    assert mock_log.call_args.kwargs == {"resana": True, "drive": True}


def test_export_logs_token_status_only_for_used_services(workspace, user):
    """No token is flagged for a source or destination the workspace doesn't use."""
    workspace.source_type = "osmose"
    workspace.save(update_fields=["source_type"])

    with (
        _patched_boundaries(),
        patch("core.processing.tasks.log_token_status") as mock_log,
    ):
        export(_payload(workspace, user))  # pylint: disable=no-value-for-parameter

    _logger, message, logged_user = mock_log.call_args.args
    assert message == f"Before migrating workspace {workspace.id}"
    assert logged_user.pk == user.pk
    assert mock_log.call_args.kwargs == {"resana": False, "drive": False}
