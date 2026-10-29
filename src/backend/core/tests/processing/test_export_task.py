"""Tests for the generic export Celery task."""

# pylint: disable=redefined-outer-name  # pytest fixtures intentionally shadow outer names

from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import pytest

from core.backends.destination import FileExportStatus
from core.backends.source import SourceFolder
from core.factories import UserFactory, WorkspaceFactory
from core.models import User, Workspace
from core.processing.plan import build_plan
from core.processing.tasks import export
from core.sources.resana.token_manager import ResanaTokenExpired


@pytest.fixture(autouse=True)
def work_directory(settings, tmp_path):
    """Keep export task filesystem operations inside a temporary directory."""
    settings.APP_WORK_DIR = str(tmp_path)


@pytest.fixture(autouse=True)
def _rejected_files_bucket():
    """Keep the rejected files cleanup away from S3."""
    with patch("core.processing.rejected_files.ArchiveManager"):
        yield


@pytest.fixture(autouse=True)
def migration_run():
    """Keep the run's working rows out of the database: workspaces are mocks here."""
    with (
        patch("core.processing.tasks.create_migration_run") as create,
        patch("core.processing.tasks.delete_migration_run") as delete,
        patch("core.processing.tasks.save_run_files_stats") as save_stats,
        patch("core.processing.tasks.drop_unlinked_kept_files") as drop_kept,
        patch("core.processing.tasks.failed_downloads", return_value=[]) as failed,
    ):
        files = MagicMock()
        files.count.return_value = 0
        create.side_effect = lambda workspace, task_id, plan: SimpleNamespace(
            workspace=workspace, exports_state={}, files=files
        )
        yield SimpleNamespace(
            create=create,
            delete=delete,
            save_stats=save_stats,
            drop_kept=drop_kept,
            failed=failed,
            files=files,
        )


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


def _run_export(
    workspace, user, dest_backends=None, source_folder=None, setup_creator=None
):
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
        mock_folder_creator_cls.return_value.get_workspace_path.return_value = (
            "/tmp/ws-1"
        )
        if setup_creator:
            setup_creator(mock_folder_creator_cls.return_value)
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
        mock_fc.return_value.get_workspace_path.return_value = "/tmp/ws-1"
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

    _, creator, _ = _run_export(workspace, user, source_folder=folder_tree)

    migration_run.create.assert_called_once_with(
        workspace, ANY, build_plan(folder_tree)
    )
    creator.prepare_folder.assert_called_once_with(workspace, ANY)
    assert creator.prepare_folder.call_args.args[1].workspace is workspace
    migration_run.delete.assert_called_once_with(migration_run.create.call_args.args[1])


@pytest.mark.django_db
def test_export_deletes_the_migration_run_when_it_fails(migration_run):
    """The working rows are purged whatever the outcome of the run."""
    with pytest.raises(RuntimeError, match="drive"):
        _run_file_by_file_export(
            _saved_workspace("drive"),
            UserFactory(),
            [_destination("drive")],
            failures={"drive": RuntimeError("Drive down")},
        )

    migration_run.delete.assert_called_once()


@pytest.mark.django_db
@pytest.mark.parametrize("fails", [False, True], ids=["success", "failure"])
def test_export_keeps_the_files_stats_before_purging_the_run(migration_run, fails):
    """The analytics stats are read from the run's rows, so before they go."""
    calls = []
    migration_run.save_stats.side_effect = lambda task_id, run: calls.append(
        ("stats", task_id, run)
    )
    migration_run.delete.side_effect = lambda task_id: calls.append(("delete",))
    failures = {"archive": RuntimeError("down")} if fails else None
    workspace = _saved_workspace("archive")

    try:
        _run_file_by_file_export(
            workspace, UserFactory(), [_destination("archive")], failures=failures
        )
    except RuntimeError:
        assert fails

    task_id = migration_run.create.call_args.args[1]
    assert calls == [("stats", task_id, ANY), ("delete",)]
    assert calls[0][2].workspace == workspace


@pytest.mark.django_db
@pytest.mark.parametrize("fails", [False, True], ids=["success", "failure"])
def test_export_drops_the_unlinked_kept_files_before_purging_the_run(
    migration_run, fails
):
    """The files kept for a destination that failed are deleted, while the
    run's rows still tell which ones it kept."""
    calls = []
    migration_run.drop_kept.side_effect = lambda run: calls.append(("drop", run))
    migration_run.delete.side_effect = lambda task_id: calls.append(("delete",))
    failures = {"archive": RuntimeError("down")} if fails else None
    workspace = _saved_workspace("archive")

    try:
        _run_file_by_file_export(
            workspace, UserFactory(), [_destination("archive")], failures=failures
        )
    except RuntimeError:
        assert fails

    assert calls == [("drop", ANY), ("delete",)]
    assert calls[0][1].workspace == workspace


@pytest.mark.django_db
def test_export_calls_prepare_export_before_downloading():
    """The members are fetched first: the destinations close with them."""
    workspace = WorkspaceFactory()
    calls = []

    def record_download(creator):
        creator.prepare_folder.side_effect = lambda *args: calls.append("download")

    source_backend, creator = _run_export_with_creator(
        workspace,
        UserFactory(),
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


def test_export_persists_download_errors_on_workspace(workspace, user, migration_run):
    """export() persists the run's failed downloads onto the workspace's download_errors."""
    failed_files = [{"name": "broken.docx", "error": "403 Forbidden"}]
    migration_run.failed.return_value = failed_files
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
        mock_fc.return_value.get_workspace_path.return_value = "/tmp/ws-1"
        mock_dr.get_all.return_value = []

        export({"workspace": {"id": "ws-1"}, "user": {"id": "user-1"}})  # pylint: disable=no-value-for-parameter

    assert workspace.download_errors == failed_files
    workspace.save.assert_called()


def test_export_keeps_earlier_download_errors_when_the_source_token_expires(
    workspace, user, migration_run
):
    """An expired source token stops the run with that error, so the user is
    asked to log in again, and keeps the files that failed before it."""
    failed_files = [
        {"name": "doc0.pdf", "error": "504 Gateway Timeout"},
        {"name": "doc1.pdf", "error": "Resana offline token is no longer valid."},
    ]
    migration_run.failed.return_value = failed_files
    migration_run.files.iterator.return_value = [SimpleNamespace()]
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
        mock_fc.return_value.download_file.side_effect = ResanaTokenExpired(
            "Resana offline token is no longer valid."
        )
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
        mock_fc.return_value.get_workspace_path.return_value = "/tmp/ws-1"
        mock_dr.get_all.return_value = []

        export({"workspace": {"id": "ws-1"}, "user": {"id": "user-1"}})  # pylint: disable=no-value-for-parameter

    workspace.save.assert_not_called()


def test_export_saves_download_errors_with_update_fields(
    workspace, user, settings, migration_run
):
    """export() saves download_errors with update_fields=["download_errors"] only."""
    settings.MIGRATION_FILE_LIMIT_PER_WORKSPACE = 0
    migration_run.failed.return_value = [
        {"name": "broken.docx", "error": "403 Forbidden"}
    ]
    migration_run.files.count.return_value = 2
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
        mock_fc.return_value.get_workspace_path.return_value = "/tmp/ws-1"
        mock_dr.get_all.return_value = []

        export({"workspace": {"id": "ws-1"}, "user": {"id": "user-1"}})  # pylint: disable=no-value-for-parameter

    workspace.save.assert_called_once_with(update_fields=["download_errors"])


def test_export_fails_when_all_downloads_fail(workspace, user, migration_run):
    """export() raises instead of returning True when every download failed,
    and no destination is closed."""
    migration_run.failed.return_value = [
        {"name": "a.docx", "error": "boom"},
        {"name": "b.docx", "error": "boom"},
    ]
    migration_run.files.count.return_value = 2
    with (
        patch("core.processing.tasks.FileExports") as exports_cls,
        patch("core.models.Workspace.objects.get", return_value=workspace),
        patch("core.models.User.objects.get", return_value=user),
        patch("core.processing.tasks.SourceManager") as mock_sm,
        patch("core.processing.tasks.FolderCreator") as mock_fc,
        patch("core.processing.tasks.DestinationRegistry") as mock_dr,
    ):
        mock_sm.return_value.get_backend.return_value.get_workspace_structure.return_value = SourceFolder(
            name="root"
        )
        mock_fc.return_value.get_workspace_path.return_value = "/tmp/ws-1"
        mock_dr.get_all.return_value = [_destination("archive")]

        with pytest.raises(RuntimeError):
            export({"workspace": {"id": "ws-1"}, "user": {"id": "user-1"}})  # pylint: disable=no-value-for-parameter

    exports_cls.return_value.close.assert_not_called()


@pytest.mark.django_db
def test_export_calls_each_pending_destination():
    """export() hands the run to every destination whose status is PENDING."""
    user = UserFactory()
    dest_archive, dest_resana = _destination("archive"), _destination("resana")

    exports_cls, _ = _run_file_by_file_export(
        _saved_workspace("archive", "resana"), user, [dest_archive, dest_resana]
    )

    exports_cls.assert_called_once_with(ANY, user, [dest_archive, dest_resana])


@pytest.mark.django_db
def test_export_skips_non_pending_destinations():
    """export() skips destinations whose status is not PENDING."""
    user = UserFactory()
    workspace = WorkspaceFactory(
        destination_statuses={
            "archive": Workspace.Status.SUCCESS,
            "resana": Workspace.Status.PENDING,
        }
    )
    dest_archive, dest_resana = _destination("archive"), _destination("resana")

    exports_cls, _ = _run_file_by_file_export(
        workspace, user, [dest_archive, dest_resana]
    )

    exports_cls.assert_called_once_with(ANY, user, [dest_resana])


# ---------------------------------------------------------------------------
# File-by-file destinations
# ---------------------------------------------------------------------------


def _destination(name):
    destination = MagicMock()
    destination.name = name
    return destination


def _saved_workspace(*pending):
    """A saved workspace whose given destinations are pending."""
    return WorkspaceFactory(
        destination_statuses=dict.fromkeys(pending, Workspace.Status.PENDING)
    )


def _run_file_by_file_export(
    workspace, user, dest_backends, failures=None, downloaded_path=None
):
    """Run export() with FileExports mocked, the creator downloading every
    row of the run to downloaded_path; return (exports class, creator)."""

    def setup_creator(creator):
        creator.download_file.return_value = downloaded_path

    with patch("core.processing.tasks.FileExports") as exports_cls:
        exports_cls.return_value.close.return_value = failures or {}
        _, creator = _run_export_with_creator(
            workspace, user, dest_backends, setup_creator
        )
    return exports_cls, creator


def _run_export_with_creator(
    workspace, user, dest_backends, setup_creator=None, setup_source=None
):
    """Run export() for a saved workspace and user, with the creator (and
    source) set up by the caller; return (source backend, creator)."""
    with (
        patch("core.processing.tasks.FolderCreator") as mock_fc,
        patch("core.processing.tasks.SourceManager") as mock_sm,
        patch("core.processing.tasks.DestinationRegistry") as mock_dr,
    ):
        source_backend = mock_sm.return_value.get_backend.return_value
        source_backend.get_workspace_structure.return_value = SourceFolder(name="root")
        if setup_source:
            setup_source(source_backend)
        if setup_creator:
            setup_creator(mock_fc.return_value)
        mock_dr.get_all.return_value = dest_backends

        export({"workspace": {"id": workspace.id}, "user": {"id": user.id}})  # pylint: disable=no-value-for-parameter
    return source_backend, mock_fc.return_value


@pytest.mark.django_db
def test_export_streams_files_to_file_by_file_destinations(tmp_path, migration_run):
    """Each file is handed over to the destinations, then dropped from the disk."""
    user = UserFactory()
    drive = _destination("drive")
    local_file = tmp_path / "doc.txt"
    local_file.write_text("content")
    row = SimpleNamespace(results={"drive": {"status": FileExportStatus.SENT}})
    migration_run.files.iterator.return_value = [row]

    with patch("core.processing.tasks.keep_refused_file") as keep:
        exports_cls, _ = _run_file_by_file_export(
            _saved_workspace("drive"), user, [drive], downloaded_path=str(local_file)
        )

    exports_cls.assert_called_once_with(ANY, user, [drive])
    exports = exports_cls.return_value
    exports.open.assert_called_once_with()
    exports.close.assert_called_once_with()
    exports.put.assert_called_once_with(row, str(local_file))
    keep.assert_not_called()
    assert not local_file.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("status", [FileExportStatus.REJECTED, FileExportStatus.FAILED])
def test_export_keeps_the_files_a_destination_did_not_take(
    tmp_path, migration_run, status
):
    """A file a destination refused or failed to receive is kept on S3, for
    the destination to hand it back to the user once it closes: the disk only
    holds the file being sent."""
    local_file = tmp_path / "doc.txt"
    local_file.write_text("content")
    row = SimpleNamespace(results={"drive": {"status": status}})
    migration_run.files.iterator.return_value = [row]

    with patch("core.processing.tasks.keep_refused_file") as keep:
        exports_cls, _ = _run_file_by_file_export(
            _saved_workspace("drive"),
            UserFactory(),
            [_destination("drive")],
            downloaded_path=str(local_file),
        )

    keep.assert_called_once_with(exports_cls.return_value.run, row, str(local_file))
    assert not local_file.exists()


@pytest.mark.django_db
def test_export_fails_when_a_destination_failed():
    """A failed destination fails the task, with its error as the cause."""
    with pytest.raises(RuntimeError, match="drive") as raised:
        _run_file_by_file_export(
            _saved_workspace("drive", "archive"),
            UserFactory(),
            [_destination("drive"), _destination("archive")],
            failures={"drive": ValueError("401")},
        )

    assert isinstance(raised.value.__cause__, ValueError)


def _disk_full(creator, _migration_run):
    creator.prepare_folder.side_effect = OSError("disk full")


def _every_download_failed(creator, migration_run):
    creator.get_workspace_path.return_value = "/tmp/ws-1"
    migration_run.failed.return_value = [{"id": "f1", "error": "403"}]
    migration_run.files.count.return_value = 1


@pytest.mark.parametrize(
    "setup_creator,error",
    [(_disk_full, "disk full"), (_every_download_failed, "failed to download")],
)
@pytest.mark.django_db
def test_export_aborts_file_by_file_destinations_when_the_run_fails(
    migration_run, setup_creator, error
):
    """A run failing before the end leaves no destination half open."""
    with (
        patch("core.processing.tasks.FileExports") as exports_cls,
        pytest.raises((OSError, RuntimeError), match=error),
    ):
        _run_export_with_creator(
            _saved_workspace("drive"),
            UserFactory(),
            [_destination("drive")],
            lambda creator: setup_creator(creator, migration_run),
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


def _run_export_with_tracker(workspace, user, dest_backends, failures=None):
    """Run export() with standard mocks; return (creator, source folder)."""
    folder = SourceFolder(name="root")
    with (
        patch("core.processing.tasks.FileExports") as exports_cls,
        patch("core.models.Workspace.objects.get", return_value=workspace),
        patch("core.models.User.objects.get", return_value=user),
        patch("core.processing.tasks.SourceManager") as mock_sm,
        patch("core.processing.tasks.FolderCreator") as mock_fc,
        patch("core.processing.tasks.DestinationRegistry") as mock_dr,
    ):
        mock_sm.return_value.get_backend.return_value.get_workspace_structure.return_value = folder
        creator = mock_fc.return_value
        creator.get_workspace_path.return_value = "/tmp/ws-1"
        mock_dr.get_all.return_value = dest_backends
        exports_cls.return_value.close.return_value = failures or {}

        export({"workspace": {"id": "ws-1"}, "user": {"id": "user-1"}})  # pylint: disable=no-value-for-parameter
    return creator, folder


def test_export_feeds_integrity_tracker_and_saves_it(workspace, user, mock_tracker_cls):
    """export() gives the tracker the kept files, the retrieval and the pending
    destinations, then saves the report of a successful run."""
    dest_archive = _destination("archive")
    dest_drive = _destination("drive")
    workspace.get_destination_status.side_effect = lambda name: (
        Workspace.Status.SUCCESS if name == "archive" else Workspace.Status.PENDING
    )

    _, folder = _run_export_with_tracker(workspace, user, [dest_archive, dest_drive])

    mock_tracker_cls.assert_called_once_with(workspace, folder)
    tracker = mock_tracker_cls.return_value
    tracker.set_kept_files.assert_called_once_with(folder)
    tracker.set_retrieval.assert_called_once_with(ANY)
    assert tracker.set_retrieval.call_args.args[0].workspace is workspace
    tracker.add_exported_destination.assert_called_once_with(dest_drive)
    tracker.save.assert_called_once_with(ANY, run_failed=False)


def test_export_saves_integrity_report_when_a_destination_fails(
    workspace, user, mock_tracker_cls
):
    """A failing destination is still checked and the original error propagates."""
    dest_drive = _destination("drive")

    with pytest.raises(RuntimeError, match="drive") as raised:
        _run_export_with_tracker(
            workspace, user, [dest_drive], failures={"drive": ValueError("down")}
        )

    assert isinstance(raised.value.__cause__, ValueError)

    tracker = mock_tracker_cls.return_value
    tracker.add_exported_destination.assert_called_once_with(dest_drive)
    tracker.save.assert_called_once_with(ANY, run_failed=True)


def test_export_saves_integrity_report_when_all_downloads_fail(
    workspace, user, mock_tracker_cls, migration_run
):
    """The report is also saved when the run stops during retrieval."""
    migration_run.failed.return_value = [
        {"id": "f1", "name": "a", "path": "a", "error": "x"}
    ]
    migration_run.files.count.return_value = 1

    with pytest.raises(RuntimeError, match="failed to download"):
        _run_export_with_tracker(workspace, user, [])

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
        mock_fc.return_value.get_workspace_path.return_value = "/tmp/ws-1"
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
