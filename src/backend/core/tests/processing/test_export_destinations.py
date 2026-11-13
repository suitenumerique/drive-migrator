"""Tests for the export task handing a run over to its destinations."""

from types import SimpleNamespace
from unittest.mock import ANY, PropertyMock, call, patch

import pytest

from core.backends.destination import FileExportStatus
from core.factories import UserFactory, WorkspaceFactory
from core.models import Workspace
from core.tests.processing.export_helpers import (
    DRIVE_DOWN,
    make_destination,
    run_export_with_creator,
    run_file_by_file_export,
    saved_workspace,
)

pytestmark = pytest.mark.usefixtures(
    "work_directory", "rejected_files_bucket", "migration_run"
)


@pytest.mark.django_db
def test_export_hands_the_run_to_each_pending_destination():
    """Only the destinations still pending receive the run."""
    workspace = WorkspaceFactory(
        destination_statuses={
            "archive": Workspace.Status.SUCCESS,
            "resana": Workspace.Status.PENDING,
        }
    )
    user = UserFactory()
    archive, resana = make_destination("archive"), make_destination("resana")

    exports_cls, _ = run_file_by_file_export(workspace, user, [archive, resana])

    # Built by the start of the run, then its finish: it has no file.
    assert exports_cls.call_args_list == [call(ANY, user, [resana])] * 2


@pytest.mark.django_db
def test_export_streams_files_to_the_destinations(tmp_path, migration_run):
    """Each file is handed over to the destinations, then dropped from the
    disk."""
    workspace, user = saved_workspace("drive"), UserFactory()
    drive = make_destination("drive")
    local_file = tmp_path / "doc.txt"
    local_file.write_text("content")
    row = SimpleNamespace(results={"drive": {"status": FileExportStatus.SENT}})
    migration_run.files.iterator.return_value = [row]

    with patch("core.processing.tasks.keep_refused_file") as keep:
        exports_cls, _ = run_file_by_file_export(
            workspace, user, [drive], downloaded_path=str(local_file)
        )

    # Built by the start of the run, its file, then its finish.
    assert exports_cls.call_args_list == [call(ANY, user, [drive])] * 3
    exports = exports_cls.return_value
    exports.open.assert_called_once_with()
    exports.close.assert_called_once_with()
    exports.put.assert_called_once_with(row, str(local_file))
    keep.assert_not_called()
    assert not local_file.exists()


@pytest.mark.django_db
def test_export_downloads_nothing_once_no_destination_receives(migration_run):
    """With every destination disabled during the run, e.g. on a revoked
    token, the files left are not downloaded for nothing: the run fails when
    it closes."""
    workspace, user = saved_workspace("drive"), UserFactory()
    migration_run.files.iterator.return_value = [SimpleNamespace(position=0)]
    creators = []

    with (
        patch("core.processing.tasks.FileExports") as exports_cls,
        pytest.raises(RuntimeError, match="drive"),
    ):
        # Still receiving when the run opens, no more for its file.
        type(exports_cls.return_value).receiving = PropertyMock(
            side_effect=[True, False]
        )
        exports_cls.return_value.close.return_value = DRIVE_DOWN
        run_export_with_creator(
            workspace, user, [make_destination("drive")], creators.append
        )

    creators[0].download_file.assert_not_called()
    exports_cls.return_value.put.assert_not_called()


@pytest.mark.django_db
def test_export_fails_at_once_when_no_destination_opens():
    """Every destination failed to open, e.g. all of them unreachable: the
    run fails before queuing a single file, with the error of a destination
    as its cause."""
    workspace, user = saved_workspace("drive"), UserFactory()
    drive_down = RuntimeError("401 Unauthorized")
    with (
        patch("core.processing.tasks.FileExports") as exports_cls,
        pytest.raises(RuntimeError, match="No destination") as raised,
    ):
        exports_cls.return_value.receiving = False
        exports_cls.return_value.open.return_value = {"drive": drive_down}
        run_export_with_creator(
            workspace, user, [make_destination("drive")], lambda creator: None
        )

    assert raised.value.__cause__ is drive_down
    exports_cls.return_value.abort.assert_called_once_with()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "results",
    [
        {"drive": {"status": FileExportStatus.REJECTED}},
        {"drive": {"status": FileExportStatus.FAILED}},
    ],
    ids=["rejected", "failed"],
)
def test_export_keeps_the_files_a_destination_did_not_take(
    tmp_path, migration_run, results
):
    """A file a destination refused or failed to receive is kept on S3, for
    the destination to hand it back to the user once it closes: the disk only
    holds the file being sent."""
    workspace, user = saved_workspace("drive"), UserFactory()
    local_file = tmp_path / "doc.txt"
    local_file.write_text("content")
    row = SimpleNamespace(results=results)
    migration_run.files.iterator.return_value = [row]

    with patch("core.processing.tasks.keep_refused_file") as keep:
        exports_cls, _ = run_file_by_file_export(
            workspace,
            user,
            [make_destination("drive")],
            downloaded_path=str(local_file),
        )

    keep.assert_called_once_with(exports_cls.return_value.run, row, str(local_file))
    assert not local_file.exists()


@pytest.mark.django_db
def test_export_drops_a_downloaded_file_its_hand_over_failed(tmp_path, migration_run):
    """An error handing a file over leaves it on no worker's disk."""
    workspace, user = saved_workspace("drive"), UserFactory()
    local_file = tmp_path / "doc.txt"
    local_file.write_text("content")
    migration_run.files.iterator.return_value = [
        SimpleNamespace(position=0, results={})
    ]

    def setup_creator(creator):
        creator.download_file.return_value = str(local_file)

    with patch("core.processing.tasks.FileExports") as exports_cls:
        exports_cls.return_value.put.side_effect = RuntimeError("database down")
        exports_cls.return_value.close.return_value = {}
        run_export_with_creator(
            workspace, user, [make_destination("drive")], setup_creator
        )

    assert not local_file.exists()


@pytest.mark.django_db
def test_a_hand_over_error_is_kept_even_with_its_file_gone(tmp_path, migration_run):
    """The file gone already does not hide the error of its hand over."""
    workspace, user = saved_workspace("drive"), UserFactory()
    local_file = tmp_path / "doc.txt"
    migration_run.files.iterator.return_value = [
        SimpleNamespace(position=0, results={})
    ]

    def setup_creator(creator):
        creator.download_file.return_value = str(local_file)

    with (
        patch("core.processing.tasks.FileExports") as exports_cls,
        patch("core.processing.tasks.record_file_error") as record_error,
    ):
        exports_cls.return_value.put.side_effect = RuntimeError("Drive down")
        exports_cls.return_value.close.return_value = {}
        run_export_with_creator(
            workspace, user, [make_destination("drive")], setup_creator
        )

    assert str(record_error.call_args.args[1]) == "Drive down"


@pytest.mark.django_db
def test_export_fails_when_a_destination_failed():
    """A failed destination fails the task, its error as the cause."""
    workspace, user = saved_workspace("drive"), UserFactory()
    with pytest.raises(RuntimeError, match="drive") as raised:
        run_file_by_file_export(
            workspace,
            user,
            [make_destination("drive")],
            failures={"drive": ValueError("401")},
        )

    assert raised.value.names == ["drive"]
    assert isinstance(raised.value.__cause__, ValueError)


def _disk_full(creator, _migration_run):
    creator.prepare_folder.side_effect = OSError("disk full")


def _every_download_failed(creator, migration_run):
    creator.get_workspace_path.return_value = "/tmp/ws-1"
    migration_run.failed.return_value = [{"id": "f1", "error": "403"}]
    migration_run.files.count.return_value = 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "setup_creator,error",
    [(_disk_full, "disk full"), (_every_download_failed, "failed to download")],
)
def test_export_aborts_the_destinations_when_the_run_fails(
    migration_run, setup_creator, error
):
    """A run failing before the end leaves no destination half open."""
    workspace, user = saved_workspace("drive"), UserFactory()
    with (
        patch("core.processing.tasks.FileExports") as exports_cls,
        pytest.raises((OSError, RuntimeError), match=error),
    ):
        run_export_with_creator(
            workspace,
            user,
            [make_destination("drive")],
            lambda creator: setup_creator(creator, migration_run),
        )

    exports_cls.return_value.abort.assert_called_once_with()
    exports_cls.return_value.close.assert_not_called()
