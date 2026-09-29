"""Tests for DriveDestinationBackend.check_integrity()."""

# pylint: disable=redefined-outer-name  # pytest fixtures intentionally shadow outer names

import os
from unittest.mock import MagicMock, patch

import pytest

from core.destinations.drive.backend import DriveDestinationBackend
from core.models import Workspace


@pytest.fixture(autouse=True)
def _patch_mails_manager():
    """Prevent real MailsManager calls (DB access) across all tests in this module."""
    with patch("core.destinations.drive.backend.MailsManager"):
        yield


@pytest.fixture(autouse=True)
def _drive_settings(settings):
    settings.DRIVE_AUTH_MODE = "service_account"
    settings.DRIVE_SHARE_MEMBERS = False
    settings.DRIVE_INTEGRITY_ANALYSIS_TIMEOUT = 30
    settings.DRIVE_INTEGRITY_ANALYSIS_POLL_INTERVAL = 10


@pytest.fixture()
def mock_backend():
    """Drive HTTP client whose created items get an id derived from their name."""
    with patch("core.destinations.drive.backend.DriveServiceAccountBackend") as cls:
        backend = cls.return_value
        backend.create_folder.return_value = {"id": "root"}
        backend.create_subfolder.side_effect = lambda name, parent_id: {
            "id": f"folder-{name}"
        }
        backend.create_file_item.side_effect = lambda name, parent_id, item_id: {
            "id": f"id-{name}",
            "policy": "https://s3.example.com/x",
        }
        backend.list_children.return_value = []
        backend.get_item.return_value = None
        yield backend


def _make_workspace(workspace_id="ws-1", members=None):
    workspace = MagicMock(spec=Workspace)
    workspace.id = workspace_id
    workspace.title = "My Workspace"
    workspace.destination_statuses = {}
    workspace.migration_user = None
    workspace.members = members or []
    return workspace


def _write(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)


def _drive_file(item_id, title, upload_state="ready", size=3):
    return {
        "id": item_id,
        "title": title,
        "type": "file",
        "upload_state": upload_state,
        "size": size,
    }


def _drive_folder(item_id, title):
    return {"id": item_id, "title": title, "type": "folder"}


def _export_then_check(tmp_path, workspace=None, wait_for_analysis=True):
    destination = DriveDestinationBackend()
    workspace = workspace or _make_workspace()
    destination.export(workspace, MagicMock(), str(tmp_path))
    return destination.check_integrity(workspace, str(tmp_path), wait_for_analysis)


# ---------------------------------------------------------------------------
# Matching local files with Drive items
# ---------------------------------------------------------------------------


def test_ready_file_with_same_size_is_ok(tmp_path, mock_backend):
    """A file found on Drive, ready and with the local size, is ok."""
    _write(tmp_path / "report.pdf", 3)
    mock_backend.list_children.return_value = [
        _drive_file("id-report.pdf", "report.pdf")
    ]

    result = _export_then_check(tmp_path)

    assert result == {
        "files": {
            "report.pdf": {
                "item_id": "id-report.pdf",
                "title": "report.pdf",
                "upload_state": "ready",
                "size": 3,
                "stage": "ok",
            }
        },
        "extra": [],
    }


def test_file_renamed_by_drive_is_matched_by_item_id(tmp_path, mock_backend):
    """Drive may rename a duplicate title; matching relies on the item id."""
    _write(tmp_path / "report.pdf", 3)
    mock_backend.list_children.return_value = [
        _drive_file("id-report.pdf", "report (1).pdf")
    ]

    result = _export_then_check(tmp_path)

    entry = result["files"]["report.pdf"]
    assert entry["stage"] == "ok"
    assert entry["title"] == "report (1).pdf"


def test_nested_file_is_found_in_its_drive_folder(tmp_path, mock_backend):
    """check_integrity() walks Drive subfolders recursively."""
    _write(tmp_path / "docs" / "a.txt", 3)
    mock_backend.list_children.side_effect = lambda folder_id: {
        "root": [_drive_folder("folder-docs", "docs")],
        "folder-docs": [_drive_file("id-a.txt", "a.txt")],
    }[folder_id]

    result = _export_then_check(tmp_path)

    assert result["files"][os.path.join("docs", "a.txt")]["stage"] == "ok"


def test_size_mismatch_is_reported(tmp_path, mock_backend):
    """A ready file whose Drive size differs from the local one is size_mismatch."""
    _write(tmp_path / "report.pdf", 3)
    mock_backend.list_children.return_value = [
        _drive_file("id-report.pdf", "report.pdf", size=1)
    ]

    result = _export_then_check(tmp_path)

    assert result["files"]["report.pdf"]["stage"] == "size_mismatch"


@pytest.mark.parametrize("upload_state", ["suspicious"])
def test_file_not_ready_on_drive_is_reported(tmp_path, mock_backend, upload_state):
    """A file blocked by the malware analysis is not_ready, raw state kept."""
    _write(tmp_path / "report.pdf", 3)
    mock_backend.list_children.return_value = [
        _drive_file("id-report.pdf", "report.pdf", upload_state=upload_state)
    ]

    result = _export_then_check(tmp_path)

    entry = result["files"]["report.pdf"]
    assert entry["stage"] == "not_ready"
    assert entry["upload_state"] == upload_state


@pytest.mark.parametrize("drive_size,stage", [(3, "ok"), (1, "size_mismatch")])
def test_file_too_large_to_analyze_is_checked_like_a_ready_file(
    tmp_path, mock_backend, drive_size, stage
):
    """Drive keeps a file too large to analyze downloadable: only its size counts."""
    _write(tmp_path / "video.mp4", 3)
    mock_backend.list_children.return_value = [
        _drive_file(
            "id-video.mp4",
            "video.mp4",
            upload_state="file_too_large_to_analyze",
            size=drive_size,
        )
    ]

    result = _export_then_check(tmp_path)

    entry = result["files"]["video.mp4"]
    assert entry["stage"] == stage
    assert entry["upload_state"] == "file_too_large_to_analyze"


def test_file_missing_from_listing_and_unknown_to_drive_is_not_created(
    tmp_path, mock_backend
):
    """An expected item absent from the listing and 404 on GET is not_created."""
    _write(tmp_path / "report.pdf", 3)

    result = _export_then_check(tmp_path)

    mock_backend.get_item.assert_called_once_with("id-report.pdf")
    assert result["files"]["report.pdf"] == {
        "item_id": "id-report.pdf",
        "stage": "not_created",
    }


def test_file_missing_from_listing_but_pending_on_drive_is_pending(
    tmp_path, mock_backend
):
    """The listing hides pending items: a GET tells a pending item from a missing one."""
    _write(tmp_path / "report.pdf", 3)
    mock_backend.get_item.return_value = _drive_file(
        "id-report.pdf", "report.pdf", upload_state="pending", size=None
    )

    result = _export_then_check(tmp_path)

    entry = result["files"]["report.pdf"]
    assert entry["stage"] == "pending"
    assert entry["upload_state"] == "pending"


def test_drive_file_without_local_counterpart_is_extra(tmp_path, mock_backend):
    """A Drive file this run did not upload is listed in extra with its Drive path."""
    mock_backend.list_children.side_effect = lambda folder_id: {
        "root": [_drive_folder("folder-docs", "docs")],
        "folder-docs": [_drive_file("dup-id", "a (1).txt")],
    }[folder_id]

    result = _export_then_check(tmp_path)

    assert result["files"] == {}
    assert result["extra"] == [
        {
            "item_id": "dup-id",
            "path": os.path.join("docs", "a (1).txt"),
            "upload_state": "ready",
            "size": 3,
        }
    ]


def test_generated_users_csv_is_flagged(tmp_path, mock_backend):
    """users_list_by_migrator.csv is written by the migrator itself, not by the source."""
    mock_backend.list_children.return_value = [
        _drive_file("id-users_list_by_migrator.csv", "users_list_by_migrator.csv")
    ]
    workspace = _make_workspace(members=[{"name": "A", "firstName": "B", "email": "c"}])

    result = _export_then_check(tmp_path, workspace=workspace)

    assert result["files"]["users_list_by_migrator.csv"]["generated"] is True


# ---------------------------------------------------------------------------
# Waiting for Drive's malware analysis
# ---------------------------------------------------------------------------


def test_analyzing_file_that_becomes_ready_is_ok(tmp_path, mock_backend):
    """Files still analyzing are polled until they settle."""
    _write(tmp_path / "report.pdf", 3)
    mock_backend.list_children.return_value = [
        _drive_file("id-report.pdf", "report.pdf", upload_state="analyzing")
    ]
    mock_backend.get_item.return_value = _drive_file("id-report.pdf", "report.pdf")

    with patch("core.destinations.drive.backend.time") as mock_time:
        mock_time.monotonic.return_value = 0
        result = _export_then_check(tmp_path)

    mock_time.sleep.assert_called_once_with(10)
    mock_backend.get_item.assert_called_once_with("id-report.pdf")
    assert result["files"]["report.pdf"]["stage"] == "ok"


def test_analyzing_file_after_timeout_is_analysis_unfinished(tmp_path, mock_backend):
    """Polling stops at DRIVE_INTEGRITY_ANALYSIS_TIMEOUT: a slow analysis is not
    a loss, the file is analysis_unfinished."""
    _write(tmp_path / "report.pdf", 3)
    analyzing = _drive_file("id-report.pdf", "report.pdf", upload_state="analyzing")
    mock_backend.list_children.return_value = [analyzing]
    mock_backend.get_item.return_value = analyzing

    with patch("core.destinations.drive.backend.time") as mock_time:
        mock_time.monotonic.side_effect = [0, 0, 10, 20, 30]
        result = _export_then_check(tmp_path)

    assert mock_time.sleep.call_count == 3
    entry = result["files"]["report.pdf"]
    assert entry["stage"] == "analysis_unfinished"
    assert entry["upload_state"] == "analyzing"


def test_no_polling_when_not_waiting_for_analysis(tmp_path, mock_backend):
    """With wait_for_analysis=False (failed run), analyzing files are not polled."""
    _write(tmp_path / "report.pdf", 3)
    mock_backend.list_children.return_value = [
        _drive_file("id-report.pdf", "report.pdf", upload_state="analyzing")
    ]

    with patch("core.destinations.drive.backend.time") as mock_time:
        result = _export_then_check(tmp_path, wait_for_analysis=False)

    mock_time.sleep.assert_not_called()
    mock_backend.get_item.assert_not_called()
    entry = result["files"]["report.pdf"]
    assert entry["stage"] == "analysis_unfinished"
    assert entry["upload_state"] == "analyzing"


@pytest.mark.parametrize("wait_for_analysis", [True, False])
def test_analyzing_file_with_wrong_size_is_size_mismatch(
    tmp_path, mock_backend, wait_for_analysis
):
    """The size is checked even while the malware analysis is unfinished."""
    _write(tmp_path / "report.pdf", 3)
    analyzing = _drive_file(
        "id-report.pdf", "report.pdf", upload_state="analyzing", size=2
    )
    mock_backend.list_children.return_value = [analyzing]
    mock_backend.get_item.return_value = analyzing

    with patch("core.destinations.drive.backend.time") as mock_time:
        mock_time.monotonic.side_effect = [0, 0, 10, 20, 30]
        result = _export_then_check(tmp_path, wait_for_analysis=wait_for_analysis)

    assert result["files"]["report.pdf"]["stage"] == "size_mismatch"


def test_blocked_file_is_not_ready_even_without_waiting(tmp_path, mock_backend):
    """Only a file still analyzing depends on the wait: a blocked one is not_ready."""
    _write(tmp_path / "report.pdf", 3)
    mock_backend.list_children.return_value = [
        _drive_file("id-report.pdf", "report.pdf", upload_state="suspicious")
    ]

    result = _export_then_check(tmp_path, wait_for_analysis=False)

    assert result["files"]["report.pdf"]["stage"] == "not_ready"


# ---------------------------------------------------------------------------
# Interrupted runs and failures
# ---------------------------------------------------------------------------


def test_file_whose_creation_failed_is_tracked_with_its_client_id(
    tmp_path, mock_backend
):
    """The item id is recorded before the request, so a failed creation is still
    checked on Drive; files after it were never attempted and are left out."""
    _write(tmp_path / "a.txt", 3)
    _write(tmp_path / "b.txt", 3)
    mock_backend.create_file_item.side_effect = RuntimeError("Drive down")
    destination = DriveDestinationBackend()
    workspace = _make_workspace()

    with pytest.raises(RuntimeError):
        destination.export(workspace, MagicMock(), str(tmp_path))
    result = destination.check_integrity(workspace, str(tmp_path), False)

    client_id = mock_backend.create_file_item.call_args.kwargs["item_id"]
    mock_backend.get_item.assert_called_once_with(client_id)
    assert result["files"] == {"a.txt": {"item_id": client_id, "stage": "not_created"}}


def test_listing_failure_marks_uploaded_files_unverified(tmp_path, mock_backend):
    """If Drive can't be re-read, uploaded files are unverified and the error kept."""
    _write(tmp_path / "report.pdf", 3)
    mock_backend.list_children.side_effect = RuntimeError("token expired")

    result = _export_then_check(tmp_path)

    assert result == {
        "files": {
            "report.pdf": {"item_id": "id-report.pdf", "stage": "unverified"},
        },
        "extra": [],
        "error": "token expired",
    }


def test_nothing_to_check_when_drive_export_did_not_run_for_workspace(
    tmp_path, mock_backend
):
    """The backend is a cached singleton: a previous run's upload log for another
    workspace must not leak into this workspace's report."""
    _write(tmp_path / "report.pdf", 3)
    destination = DriveDestinationBackend()
    destination.export(_make_workspace("ws-1"), MagicMock(), str(tmp_path))

    result = destination.check_integrity(_make_workspace("ws-2"), str(tmp_path), True)

    assert result == {"files": {}, "extra": []}
    mock_backend.list_children.assert_not_called()


def test_nothing_to_check_when_root_folder_creation_failed(tmp_path, mock_backend):
    """If the root folder could not be created, nothing was sent to Drive."""
    mock_backend.create_folder.side_effect = RuntimeError("Drive down")
    destination = DriveDestinationBackend()
    workspace = _make_workspace()

    with pytest.raises(RuntimeError):
        destination.export(workspace, MagicMock(), str(tmp_path))
    result = destination.check_integrity(workspace, str(tmp_path), False)

    assert result == {"files": {}, "extra": []}
