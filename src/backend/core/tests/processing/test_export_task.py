"""Tests for the generic export Celery task."""

# pylint: disable=redefined-outer-name  # pytest fixtures intentionally shadow outer names

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

from django.utils import timezone

import pytest
from django_celery_results.models import TaskResult

from core.backends.source import SourceFolder
from core.models import ExtraTaskInfo, User, Workspace
from core.processing.plan import build_plan
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


@pytest.fixture(autouse=True)
def work_directory(settings, tmp_path):
    """Keep export task filesystem operations inside a temporary directory."""
    settings.APP_WORK_DIR = str(tmp_path)


@pytest.fixture(autouse=True)
def migration_run():
    """Keep the run's working rows out of the database: workspaces are mocks here."""
    with (
        patch("core.processing.tasks.create_migration_run") as create,
        patch("core.processing.tasks.delete_migration_run") as delete,
    ):
        create.side_effect = lambda workspace, task_id, plan: SimpleNamespace(
            workspace=workspace
        )
        yield SimpleNamespace(create=create, delete=delete)


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


def test_export_builds_local_folder_from_its_migration_run(
    workspace, user, migration_run
):
    """export() plans the tree into the run of its task, downloads the run into
    the local folder, then purges the run."""
    folder_tree = SourceFolder(name="root", children=[SourceFolder(name="sub")])

    source_backend, creator, _ = _run_export(workspace, user, source_folder=folder_tree)

    migration_run.create.assert_called_once_with(
        workspace, ANY, build_plan(folder_tree)
    )
    creator.create_folder.assert_called_once_with(
        workspace, ANY, source_backend, on_downloaded=None
    )
    assert creator.create_folder.call_args.args[1].workspace is workspace
    migration_run.delete.assert_called_once_with(migration_run.create.call_args.args[1])


def test_export_deletes_the_migration_run_when_it_fails(workspace, user, migration_run):
    """The working rows are purged whatever the outcome of the run."""
    dest_drive = MagicMock()
    dest_drive.name = "drive"
    dest_drive.exports_file_by_file = False
    dest_drive.export.side_effect = RuntimeError("Drive down")

    with pytest.raises(RuntimeError, match="Drive down"):
        _run_export(workspace, user, dest_backends=[dest_drive])

    migration_run.delete.assert_called_once()


def test_export_calls_prepare_export_before_downloading(workspace, user):
    """The members are fetched first: file-by-file destinations close with them."""
    calls = []

    def record_download(creator):
        creator.create_folder.side_effect = lambda *args, **kwargs: calls.append(
            "download"
        )
        creator.failed_files = []

    source_backend, creator = _run_export_with_creator(
        workspace,
        user,
        [],
        record_download,
        setup_source=lambda source: setattr(
            source.prepare_export, "side_effect", lambda *args: calls.append("prepare")
        ),
    )

    source_backend.prepare_export.assert_called_once_with(
        workspace, creator.get_workspace_path.return_value
    )
    assert calls == ["prepare", "download"]


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


def test_export_keeps_earlier_download_errors_when_the_source_token_expires(
    workspace, user
):
    """An expired source token stops the run with that error, so the user is
    asked to log in again, and keeps the files that failed before it."""
    failed_files = [
        {"name": "doc0.pdf", "error": "504 Gateway Timeout"},
        {"name": "doc1.pdf", "error": "Resana offline token is no longer valid."},
    ]
    with (
        patch("core.models.Workspace.objects.get", return_value=workspace),
        patch("core.models.User.objects.get", return_value=user),
        patch("core.processing.tasks.SourceManager") as mock_sm,
        patch("core.processing.tasks.FolderCreator") as mock_fc,
        patch("core.processing.tasks.IntegrityTracker"),
        patch("core.processing.tasks.DestinationRegistry") as mock_dr,
    ):
        mock_sm.return_value.get_backend.return_value.get_workspace_structure.return_value = SourceFolder(
            name="root"
        )
        mock_fc.return_value.create_folder.side_effect = ResanaTokenExpired(
            "Resana offline token is no longer valid."
        )
        mock_fc.return_value.failed_files = failed_files
        mock_dr.get_all.return_value = []

        with pytest.raises(ResanaTokenExpired):
            export({"workspace": {"id": "ws-1"}, "user": {"id": "user-1"}})  # pylint: disable=no-value-for-parameter

    assert workspace.download_errors == failed_files


def test_export_resets_previous_run_errors_before_starting(workspace, user):
    """export() clears the errors of a previous run before the source is called."""
    source_backend = MagicMock()

    def check_reset(_workspace):
        workspace.reset_migration_errors.assert_called_once_with()
        raise RuntimeError("stop")

    source_backend.begin_export.side_effect = check_reset
    with (
        patch("core.models.Workspace.objects.get", return_value=workspace),
        patch("core.models.User.objects.get", return_value=user),
        patch("core.processing.tasks.SourceManager") as mock_sm,
        pytest.raises(RuntimeError, match="stop"),
    ):
        mock_sm.return_value.get_backend.return_value = source_backend
        export({"workspace": {"id": "ws-1"}, "user": {"id": "user-1"}})  # pylint: disable=no-value-for-parameter


def test_export_clears_previous_run_rejected_files_before_starting(workspace, user):
    """export() deletes the rejected files zips of a previous run before the source
    is called."""
    source_backend = MagicMock()

    with patch("core.processing.tasks.RejectedFilesArchive") as archive_cls:

        def check_clear(_workspace):
            archive_cls.clear.assert_called_once_with(workspace)
            raise RuntimeError("stop")

        source_backend.begin_export.side_effect = check_clear
        with (
            patch("core.models.Workspace.objects.get", return_value=workspace),
            patch("core.models.User.objects.get", return_value=user),
            patch("core.processing.tasks.SourceManager") as mock_sm,
            pytest.raises(RuntimeError, match="stop"),
        ):
            mock_sm.return_value.get_backend.return_value = source_backend
            export({"workspace": {"id": "ws-1"}, "user": {"id": "user-1"}})  # pylint: disable=no-value-for-parameter


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
    dest_backend.exports_file_by_file = False

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
    dest_archive.exports_file_by_file = False
    dest_resana = MagicMock()
    dest_resana.name = "resana"
    dest_resana.exports_file_by_file = False

    _run_export(workspace, user, dest_backends=[dest_archive, dest_resana])

    dest_archive.export.assert_called_once_with(workspace, user, "/tmp/ws-1")
    dest_resana.export.assert_called_once_with(workspace, user, "/tmp/ws-1")


def test_export_skips_non_pending_destinations(workspace, user):
    """export() skips destinations whose status is not PENDING."""
    dest_archive = MagicMock()
    dest_archive.name = "archive"
    dest_archive.exports_file_by_file = False
    dest_resana = MagicMock()
    dest_resana.name = "resana"
    dest_resana.exports_file_by_file = False

    def get_status(name):
        if name == "archive":
            return Workspace.Status.SUCCESS
        return Workspace.Status.PENDING

    workspace.get_destination_status.side_effect = get_status

    _run_export(workspace, user, dest_backends=[dest_archive, dest_resana])

    dest_archive.export.assert_not_called()
    dest_resana.export.assert_called_once()


# ---------------------------------------------------------------------------
# File-by-file destinations
# ---------------------------------------------------------------------------


def _file_by_file_destination(name):
    destination = MagicMock()
    destination.name = name
    destination.exports_file_by_file = True
    return destination


def _run_file_by_file_export(workspace, user, dest_backends, failures=None):
    """Run export() with FileExports mocked; return (exports class, creator)."""
    with patch("core.processing.tasks.FileExports") as exports_cls:
        exports_cls.return_value.close.return_value = failures or {}
        _, creator, _ = _run_export(workspace, user, dest_backends=dest_backends)
    return exports_cls, creator


def _run_export_with_creator(
    workspace, user, dest_backends, setup_creator, setup_source=None
):
    """Run export() with the creator (and source) set up by the caller;
    return (source backend, creator)."""
    with (
        patch("core.processing.tasks.FolderCreator") as mock_fc,
        patch("core.models.Workspace.objects.get", return_value=workspace),
        patch("core.models.User.objects.get", return_value=user),
        patch("core.processing.tasks.SourceManager") as mock_sm,
        patch("core.processing.tasks.DestinationRegistry") as mock_dr,
    ):
        source_backend = mock_sm.return_value.get_backend.return_value
        source_backend.get_workspace_structure.return_value = SourceFolder(name="root")
        if setup_source:
            setup_source(source_backend)
        setup_creator(mock_fc.return_value)
        mock_dr.get_all.return_value = dest_backends

        export({"workspace": {"id": "ws-1"}, "user": {"id": "user-1"}})  # pylint: disable=no-value-for-parameter
    return source_backend, mock_fc.return_value


def test_export_streams_files_to_file_by_file_destinations(workspace, user, tmp_path):
    """With only file-by-file destinations, each file is handed over then
    dropped from the disk, and no legacy export() runs."""
    drive = _file_by_file_destination("drive")

    exports_cls, creator = _run_file_by_file_export(workspace, user, [drive])

    exports_cls.assert_called_once_with(ANY, user, [drive])
    exports = exports_cls.return_value
    exports.open.assert_called_once_with()
    exports.close.assert_called_once_with()
    drive.export.assert_not_called()

    on_downloaded = creator.create_folder.call_args.kwargs["on_downloaded"]
    local_file = tmp_path / "doc.txt"
    local_file.write_text("content")
    on_downloaded("row", str(local_file))
    exports.put.assert_called_once_with("row", str(local_file))
    assert not local_file.exists()


def test_export_keeps_local_files_for_legacy_destinations(workspace, user):
    """A legacy destination still reads the local folder, after the others close."""
    drive = _file_by_file_destination("drive")
    archive = _destination("archive")
    calls = []
    archive.export.side_effect = lambda *args: calls.append("archive")

    with patch("core.processing.tasks.FileExports") as exports_cls:
        exports = exports_cls.return_value
        exports.close.side_effect = lambda: calls.append("close") or {}
        _, creator, _ = _run_export(workspace, user, dest_backends=[archive, drive])

    exports_cls.assert_called_once_with(ANY, user, [drive])
    creator.create_folder.assert_called_once_with(
        workspace, ANY, ANY, on_downloaded=exports.put
    )
    archive.export.assert_called_once_with(workspace, user, "/tmp/ws-1")
    assert calls == ["close", "archive"]


def test_export_fails_after_the_others_when_a_destination_failed(workspace, user):
    """A failed file-by-file destination fails the task, once every other
    destination is done."""
    drive = _file_by_file_destination("drive")
    archive = _destination("archive")

    with pytest.raises(RuntimeError, match="drive") as raised:
        _run_file_by_file_export(
            workspace, user, [archive, drive], failures={"drive": ValueError("401")}
        )

    archive.export.assert_called_once()
    assert isinstance(raised.value.__cause__, ValueError)


def _disk_full(creator):
    creator.create_folder.side_effect = OSError("disk full")


def _every_download_failed(creator):
    creator.create_folder.return_value = "/tmp/ws-1"
    creator.failed_files = [{"id": "f1", "error": "403"}]
    creator.files_count = 1
    creator.files_success = 0


@pytest.mark.parametrize(
    "setup_creator,error",
    [(_disk_full, "disk full"), (_every_download_failed, "failed to download")],
)
def test_export_aborts_file_by_file_destinations_when_the_run_fails(
    workspace, user, setup_creator, error
):
    """A run failing before the end leaves no destination half open."""
    with (
        patch("core.processing.tasks.FileExports") as exports_cls,
        pytest.raises((OSError, RuntimeError), match=error),
    ):
        _run_export_with_creator(
            workspace, user, [_file_by_file_destination("drive")], setup_creator
        )

    exports_cls.return_value.abort.assert_called_once_with()
    exports_cls.return_value.close.assert_not_called()


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
    destination.exports_file_by_file = False
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
        ANY, creator.get_workspace_path.return_value
    )
    assert tracker.set_retrieval.call_args.args[0].workspace is workspace
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
        mock_extra_task.user, mock_workspace, source_token_expired=False
    )


def test_task_failure_handler_asks_to_log_in_again_on_expired_source_token():
    """A run that failed on an expired source token tells the user to log in
    to the source again before relaunching."""
    mock_sender = MagicMock()
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
        patch(
            "core.processing.tasks.local_files_stats",
            return_value={"workspace_size_bytes": 3},
        ),
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


def test_export_logs_token_status_before_migrating(workspace, user):
    """The tokens of the services the workspace relies on are logged at task start."""
    workspace.source_type = "resana"

    with patch("core.processing.tasks.log_token_status") as mock_log:
        _run_export(workspace, user)

    mock_log.assert_called_once_with(
        ANY, "Before migrating workspace ws-1", user, resana=True, drive=True
    )


def test_export_logs_token_status_only_for_used_services(workspace, user):
    """No token is flagged for a source or destination the workspace doesn't use."""
    workspace.source_type = "osmose"
    workspace.get_destination_status.return_value = Workspace.Status.NONE

    with patch("core.processing.tasks.log_token_status") as mock_log:
        _run_export(workspace, user)

    mock_log.assert_called_once_with(
        ANY, "Before migrating workspace ws-1", user, resana=False, drive=False
    )
