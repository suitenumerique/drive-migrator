"""Tests for the generic export Celery task."""

# pylint: disable=redefined-outer-name  # pytest fixtures intentionally shadow outer names

from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import pytest
from celery.exceptions import Ignore

from core.backends.source import SourceFolder, SourceTokenExpired
from core.factories import UserFactory, WorkspaceFactory
from core.models import User, Workspace
from core.processing import tasks
from core.processing.plan import build_plan
from core.processing.tasks import export
from core.sources.resana.token_manager import ResanaTokenExpired
from core.tests.processing.export_helpers import (
    DRIVE_DOWN,
    EXPORT_DATA,
    export_and_run_tasks,
    make_destination,
    run_export_with_creator,
    run_file_by_file_export,
    saved_workspace,
)

pytestmark = pytest.mark.usefixtures(
    "work_directory", "rejected_files_bucket", "migration_run"
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

        export_and_run_tasks()

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

        export_and_run_tasks()

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


def _export_dispatching(workspace, user, dispatch_error=None):
    """Run export() alone, its first batch not queued; return what it raised,
    the dispatch, the file-by-file exports and the source."""
    with (
        patch("core.models.Workspace.objects.get", return_value=workspace),
        patch("core.models.User.objects.get", return_value=user),
        patch("core.processing.tasks.SourceManager") as mock_sm,
        patch("core.processing.tasks.FolderCreator"),
        patch("core.processing.tasks.DestinationRegistry") as mock_dr,
        patch("core.processing.tasks.FileExports") as exports_cls,
        patch("core.processing.tasks.dispatch_batch") as dispatch,
    ):
        source_backend = mock_sm.return_value.get_backend.return_value
        source_backend.get_workspace_structure.return_value = SourceFolder(name="root")
        mock_dr.get_all.return_value = [make_destination("drive")]
        dispatch.side_effect = dispatch_error
        with pytest.raises((Ignore, RuntimeError)) as raised:
            export(EXPORT_DATA)  # pylint: disable=no-value-for-parameter
    return SimpleNamespace(
        error=raised.value,
        dispatch=dispatch,
        exports=exports_cls.return_value,
        source=source_backend,
    )


def test_export_queues_the_first_batch_and_leaves_the_run_to_its_tasks(
    workspace, user, migration_run
):
    """export() starts the run and queues its first batch, then ends without a
    result of its own: the source stays locked and the run is kept for its file
    tasks, until finish_migration reports the outcome."""
    result = _export_dispatching(workspace, user)

    assert isinstance(result.error, Ignore)
    run, dispatched_user = result.dispatch.call_args.args
    assert run.workspace is workspace
    assert dispatched_user is user
    result.exports.open.assert_called_once_with()
    result.exports.abort.assert_not_called()
    result.source.finalize_export.assert_not_called()
    migration_run.delete.assert_not_called()


def test_export_undoes_the_start_when_the_first_batch_cannot_be_queued(
    workspace, user, migration_run
):
    """A broker down at the start leaves no destination open, no run kept and
    no source locked: the run is purged last, for the watchdog to see it if
    the worker dies before."""
    released = []
    migration_run.delete.side_effect = lambda *_: released.append(
        tasks.SourceManager().get_backend().finalize_export.called
    )
    result = _export_dispatching(
        workspace, user, dispatch_error=RuntimeError("broker down")
    )

    assert str(result.error) == "broker down"
    result.exports.abort.assert_called_once_with()
    assert released == [True]
    result.source.finalize_export.assert_called_once_with(workspace)


@pytest.mark.django_db
def test_export_deletes_the_migration_run_when_it_fails(migration_run):
    """The working rows are purged whatever the outcome of the run."""
    workspace, user = saved_workspace("drive"), UserFactory()
    with pytest.raises(RuntimeError, match="drive"):
        run_file_by_file_export(
            workspace, user, [make_destination("drive")], failures=DRIVE_DOWN
        )

    migration_run.delete.assert_called_once()


@pytest.mark.django_db
@pytest.mark.parametrize("fails", [False, True], ids=["success", "failure"])
def test_export_keeps_the_files_stats_before_purging_the_run(migration_run, fails):
    """The analytics stats are read from the run's rows, so before they go."""
    workspace, user = saved_workspace("drive"), UserFactory()
    calls = []
    migration_run.save_stats.side_effect = lambda task_id, run: calls.append(
        ("stats", task_id, run)
    )
    migration_run.delete.side_effect = lambda task_id: calls.append(("delete",))
    try:
        run_file_by_file_export(
            workspace,
            user,
            [make_destination("drive")],
            failures=DRIVE_DOWN if fails else None,
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
    workspace, user = saved_workspace("drive"), UserFactory()
    calls = []
    migration_run.drop_kept.side_effect = lambda run: calls.append(("drop", run))
    migration_run.delete.side_effect = lambda task_id: calls.append(("delete",))
    try:
        run_file_by_file_export(
            workspace,
            user,
            [make_destination("drive")],
            failures=DRIVE_DOWN if fails else None,
        )
    except RuntimeError:
        assert fails

    assert calls == [("drop", ANY), ("delete",)]
    assert calls[0][1].workspace == workspace


@pytest.mark.django_db
def test_export_calls_prepare_export_before_downloading():
    """The members are fetched first: file-by-file destinations close with them."""
    workspace, user = WorkspaceFactory(), UserFactory()
    calls = []

    def record_download(creator):
        creator.prepare_folder.side_effect = lambda *args: calls.append("download")

    source_backend, creator = run_export_with_creator(
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

        export_and_run_tasks()

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
        patch("core.processing.tasks.FileExports"),
    ):
        mock_sm.return_value.get_backend.return_value.get_workspace_structure.return_value = SourceFolder(
            name="root"
        )
        mock_fc.return_value.download_file.side_effect = ResanaTokenExpired(
            "Resana offline token is no longer valid."
        )
        mock_dr.get_all.return_value = [make_destination("drive")]

        with pytest.raises(SourceTokenExpired):
            export_and_run_tasks()

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
        export_and_run_tasks()


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
            export_and_run_tasks()


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

        export_and_run_tasks()

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

        export_and_run_tasks()

    workspace.save.assert_called_once_with(update_fields=["download_errors"])


# ---------------------------------------------------------------------------
# Integrity tracking
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_tracker_cls():
    with patch("core.processing.tasks.IntegrityTracker") as tracker_cls:
        # The finish rebuilds from the run the tracker export() fed.
        tracker_cls.from_run.return_value = tracker_cls.return_value
        yield tracker_cls


def _run_export_with_tracker(workspace, user, dest_backends, failures=None):
    """Run export() with standard mocks, the destinations closing with failures;
    return (creator, source folder)."""
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

        export_and_run_tasks()
    return creator, folder


def test_export_feeds_integrity_tracker_and_saves_it(workspace, user, mock_tracker_cls):
    """export() gives the tracker the kept files, the retrieval and the pending
    destinations, then saves the report of a successful run."""
    dest_archive = make_destination("archive")
    dest_drive = make_destination("drive")
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
    dest_drive = make_destination("drive")

    with pytest.raises(RuntimeError, match="drive"):
        _run_export_with_tracker(workspace, user, [dest_drive], failures=DRIVE_DOWN)

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

        export_and_run_tasks()

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
            export_and_run_tasks()

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
            export_and_run_tasks()

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
