"""Tests for DriveDestinationBackend.check_integrity()."""

# pylint: disable=redefined-outer-name  # pytest fixtures intentionally shadow outer names

import os
from unittest.mock import patch

import pytest
from requests.exceptions import HTTPError

from core import factories
from core.destinations.drive.backend import DriveDestinationBackend
from core.tests.destinations.drive.drive_api import DriveApi

pytestmark = pytest.mark.django_db


def _user():
    return factories.UserFactory(email="user@example.com")


@pytest.fixture(autouse=True)
def _drive_settings(settings, tmp_path):
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"
    settings.DRIVE_OIDC_TOKEN_ENDPOINT = "https://oidc.example.com/token"
    settings.DRIVE_OIDC_CLIENT_ID = "client-id"
    settings.DRIVE_OIDC_CLIENT_SECRET = "client-secret"
    settings.DRIVE_AUTH_MODE = "service_account"
    settings.DRIVE_SHARE_MEMBERS = False
    settings.DRIVE_RETRY_MAX_ATTEMPTS = 1
    settings.DRIVE_INTEGRITY_ANALYSIS_TIMEOUT = 30
    settings.DRIVE_INTEGRITY_ANALYSIS_POLL_INTERVAL = 10
    settings.APP_WORK_DIR = str(tmp_path / "work")
    settings.AWS_STORAGE_BUCKET_NAME = "bucket"


@pytest.fixture(autouse=True)
def s3():
    with patch("core.processing.folder_helper.boto3"):
        yield


@pytest.fixture()
def drive():
    with DriveApi() as api:
        yield api


def _make_workspace(members=None):
    return factories.WorkspaceFactory(title="My Workspace", members=members or [])


def _write(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"x" * size)


def _export(drive, tmp_path, workspace=None):  # pylint: disable=unused-argument
    destination = DriveDestinationBackend()
    workspace = workspace or _make_workspace()
    destination.export(workspace, _user(), str(tmp_path))
    return destination, workspace


def _check(destination, workspace, tmp_path, wait=True):
    return destination.check_integrity(workspace, str(tmp_path), wait)


def test_ready_file_with_same_size_is_ok(drive, tmp_path):
    """A file found on Drive, ready and with the local size, is ok."""
    _write(tmp_path / "report.pdf", 3)
    destination, workspace = _export(drive, tmp_path)

    result = _check(destination, workspace, tmp_path)

    item_id = drive.item_by_filename("report.pdf")["id"]
    assert result == {
        "files": {
            "report.pdf": {
                "item_id": item_id,
                "title": "report.pdf",
                "upload_state": "ready",
                "size": 3,
                "stage": "ok",
            }
        },
        "extra": [],
    }


def test_file_renamed_by_drive_is_matched_by_item_id(drive, tmp_path):
    """Drive may rename a duplicate title; matching relies on the item id."""
    _write(tmp_path / "report.pdf", 3)
    destination, workspace = _export(drive, tmp_path)
    drive.retitle("report.pdf", "report (1).pdf")

    result = _check(destination, workspace, tmp_path)

    entry = result["files"]["report.pdf"]
    assert entry["stage"] == "ok"
    assert entry["title"] == "report (1).pdf"


def test_nested_file_is_found_in_its_drive_folder(drive, tmp_path):
    """check_integrity() walks Drive subfolders recursively."""
    _write(tmp_path / "docs" / "a.txt", 3)
    destination, workspace = _export(drive, tmp_path)

    result = _check(destination, workspace, tmp_path)

    assert result["files"][os.path.join("docs", "a.txt")]["stage"] == "ok"


def test_size_mismatch_is_reported(drive, tmp_path):
    """A ready file whose Drive size differs from the local one is size_mismatch."""
    _write(tmp_path / "report.pdf", 3)
    destination, workspace = _export(drive, tmp_path)
    drive.resize("report.pdf", 1)

    result = _check(destination, workspace, tmp_path)

    assert result["files"]["report.pdf"]["stage"] == "size_mismatch"


@pytest.mark.parametrize("upload_state", ["suspicious"])
def test_file_not_ready_on_drive_is_reported(drive, tmp_path, upload_state):
    """A file blocked by the malware analysis is not_ready, raw state kept."""
    _write(tmp_path / "report.pdf", 3)
    destination, workspace = _export(drive, tmp_path)
    drive.set_state("report.pdf", upload_state)

    result = _check(destination, workspace, tmp_path)

    entry = result["files"]["report.pdf"]
    assert entry["stage"] == "not_ready"
    assert entry["upload_state"] == upload_state


@pytest.mark.parametrize("drive_size,stage", [(3, "ok"), (1, "size_mismatch")])
def test_file_too_large_to_analyze_is_checked_like_a_ready_file(
    drive, tmp_path, drive_size, stage
):
    """Drive keeps a file too large to analyze downloadable: only its size counts."""
    _write(tmp_path / "video.mp4", 3)
    destination, workspace = _export(drive, tmp_path)
    drive.set_state("video.mp4", "file_too_large_to_analyze")
    drive.resize("video.mp4", drive_size)

    result = _check(destination, workspace, tmp_path)

    entry = result["files"]["video.mp4"]
    assert entry["stage"] == stage
    assert entry["upload_state"] == "file_too_large_to_analyze"


def test_file_missing_from_listing_and_unknown_to_drive_is_not_created(drive, tmp_path):
    """An expected item absent from the listing and 404 on GET is not_created."""
    _write(tmp_path / "report.pdf", 3)
    destination, workspace = _export(drive, tmp_path)
    item_id = drive.item_by_filename("report.pdf")["id"]
    drive.drop("report.pdf")

    result = _check(destination, workspace, tmp_path)

    assert any(
        call.request.method == "GET" and call.request.url.rstrip("/").endswith(item_id)
        for call in drive.calls
    )
    assert result["files"]["report.pdf"] == {"item_id": item_id, "stage": "not_created"}


def test_file_missing_from_listing_but_pending_on_drive_is_pending(drive, tmp_path):
    """The listing hides pending items: a GET tells a pending item from a missing one."""
    _write(tmp_path / "report.pdf", 3)
    destination, workspace = _export(drive, tmp_path)
    drive.set_state("report.pdf", "pending")
    drive.resize("report.pdf", None)

    result = _check(destination, workspace, tmp_path)

    entry = result["files"]["report.pdf"]
    assert entry["stage"] == "pending"
    assert entry["upload_state"] == "pending"


def test_file_rejected_by_drive_carries_the_drive_error(drive, tmp_path):
    """A file Drive refused shows the Drive error code next to its stage."""
    _write(tmp_path / "page.txt", 3)
    drive.reject_create["page.txt"] = "file_type_not_allowed"
    destination = DriveDestinationBackend()
    workspace = _make_workspace()
    with pytest.raises(RuntimeError):
        destination.export(workspace, _user(), str(tmp_path))

    result = _check(destination, workspace, tmp_path)

    entry = result["files"]["page.txt"]
    assert entry["stage"] == "not_created"
    assert entry["error"] == "file_type_not_allowed"


def test_drive_file_without_local_counterpart_is_extra(drive, tmp_path):
    """A Drive file this run did not upload is listed in extra with its Drive path."""
    destination, workspace = _export(drive, tmp_path)
    root_id = destination._upload_log.root_id  # pylint: disable=protected-access
    drive.extra[root_id] = [{"id": "folder-docs", "title": "docs", "type": "folder"}]
    drive.extra["folder-docs"] = [
        {
            "id": "dup-id",
            "title": "a (1).txt",
            "type": "file",
            "upload_state": "ready",
            "size": 3,
        }
    ]

    result = _check(destination, workspace, tmp_path)

    assert result["files"] == {}
    assert result["extra"] == [
        {
            "item_id": "dup-id",
            "path": os.path.join("docs", "a (1).txt"),
            "upload_state": "ready",
            "size": 3,
        }
    ]


def test_generated_users_csv_is_flagged(drive, tmp_path):
    """users_list_by_migrator.csv is written by the migrator itself, not by the source."""
    workspace = _make_workspace(members=[{"name": "A", "firstName": "B", "email": "c"}])
    destination, workspace = _export(drive, tmp_path, workspace=workspace)

    result = _check(destination, workspace, tmp_path)

    assert result["files"]["users_list_by_migrator.csv"]["generated"] is True


def test_analyzing_file_that_becomes_ready_is_ok(drive, tmp_path):
    """Files still analyzing are polled until they settle."""
    _write(tmp_path / "report.pdf", 3)
    destination, workspace = _export(drive, tmp_path)
    drive.set_state("report.pdf", "analyzing")
    drive.promote_on_get = True
    item_id = drive.item_by_filename("report.pdf")["id"]

    with patch("core.destinations.drive.backend.time") as mock_time:
        mock_time.monotonic.return_value = 0
        result = _check(destination, workspace, tmp_path)

    mock_time.sleep.assert_called_once_with(10)
    assert any(call.request.url.rstrip("/").endswith(item_id) for call in drive.calls)
    assert result["files"]["report.pdf"]["stage"] == "ok"


def test_analyzing_file_after_timeout_is_analysis_unfinished(drive, tmp_path):
    """Polling stops at DRIVE_INTEGRITY_ANALYSIS_TIMEOUT."""
    _write(tmp_path / "report.pdf", 3)
    destination, workspace = _export(drive, tmp_path)
    drive.set_state("report.pdf", "analyzing")

    with patch("core.destinations.drive.backend.time") as mock_time:
        mock_time.monotonic.side_effect = [0, 0, 10, 20, 30]
        result = _check(destination, workspace, tmp_path)

    assert mock_time.sleep.call_count == 3
    entry = result["files"]["report.pdf"]
    assert entry["stage"] == "analysis_unfinished"
    assert entry["upload_state"] == "analyzing"


def test_no_polling_when_not_waiting_for_analysis(drive, tmp_path):
    """With wait_for_analysis=False (failed run), analyzing files are not polled."""
    _write(tmp_path / "report.pdf", 3)
    destination, workspace = _export(drive, tmp_path)
    drive.set_state("report.pdf", "analyzing")
    item_id = drive.item_by_filename("report.pdf")["id"]
    gets_before = sum(
        call.request.method == "GET" and call.request.url.rstrip("/").endswith(item_id)
        for call in drive.calls
    )

    with patch("core.destinations.drive.backend.time") as mock_time:
        result = _check(destination, workspace, tmp_path, wait=False)

    mock_time.sleep.assert_not_called()
    gets_after = sum(
        call.request.method == "GET" and call.request.url.rstrip("/").endswith(item_id)
        for call in drive.calls
    )
    assert gets_after == gets_before
    entry = result["files"]["report.pdf"]
    assert entry["stage"] == "analysis_unfinished"
    assert entry["upload_state"] == "analyzing"


@pytest.mark.parametrize("wait", [True, False])
def test_analyzing_file_with_wrong_size_is_size_mismatch(drive, tmp_path, wait):
    """The size is checked even while the malware analysis is unfinished."""
    _write(tmp_path / "report.pdf", 3)
    destination, workspace = _export(drive, tmp_path)
    drive.set_state("report.pdf", "analyzing")
    drive.resize("report.pdf", 2)

    with patch("core.destinations.drive.backend.time") as mock_time:
        mock_time.monotonic.side_effect = [0, 0, 10, 20, 30]
        result = _check(destination, workspace, tmp_path, wait=wait)

    assert result["files"]["report.pdf"]["stage"] == "size_mismatch"


def test_blocked_file_is_not_ready_even_without_waiting(drive, tmp_path):
    """Only a file still analyzing depends on the wait: a blocked one is not_ready."""
    _write(tmp_path / "report.pdf", 3)
    destination, workspace = _export(drive, tmp_path)
    drive.set_state("report.pdf", "suspicious")

    result = _check(destination, workspace, tmp_path, wait=False)

    assert result["files"]["report.pdf"]["stage"] == "not_ready"


def test_file_whose_creation_failed_is_tracked_with_its_client_id(drive, tmp_path):
    """The item id is recorded before the request, so a failed creation is still
    checked on Drive; files after it were never attempted and are left out."""
    _write(tmp_path / "a.txt", 3)
    _write(tmp_path / "b.txt", 3)
    drive.fatal_create.add("a.txt")
    destination = DriveDestinationBackend()
    workspace = _make_workspace()

    with pytest.raises(HTTPError):
        destination.export(workspace, _user(), str(tmp_path))
    result = _check(destination, workspace, tmp_path, wait=False)

    assert set(result["files"]) == {"a.txt"}
    assert result["files"]["a.txt"]["stage"] == "not_created"


def test_listing_failure_marks_uploaded_files_unverified(drive, tmp_path):
    """If Drive can't be re-read, uploaded files are unverified and the error kept."""
    _write(tmp_path / "report.pdf", 3)
    destination, workspace = _export(drive, tmp_path)
    drive.fail_listing = "token expired"

    result = _check(destination, workspace, tmp_path)

    assert result["files"]["report.pdf"]["stage"] == "unverified"
    assert result["extra"] == []
    assert result["error"] == "token expired"


def test_listing_failure_keeps_the_drive_error_of_rejected_files(drive, tmp_path):
    """Drive refusals are known locally: an unverified report still shows them."""
    _write(tmp_path / "page.txt", 3)
    _write(tmp_path / "report.pdf", 3)
    drive.reject_create["page.txt"] = "file_type_not_allowed"
    destination, workspace = _export(drive, tmp_path)
    drive.fail_listing = "token expired"

    result = _check(destination, workspace, tmp_path)

    assert result["files"]["page.txt"]["stage"] == "unverified"
    assert result["files"]["page.txt"]["error"] == "file_type_not_allowed"
    assert result["files"]["report.pdf"]["stage"] == "unverified"


def test_nothing_to_check_when_drive_export_did_not_run_for_workspace(drive, tmp_path):
    """A previous run's upload log for another workspace must not leak."""
    _write(tmp_path / "report.pdf", 3)
    destination, _workspace = _export(drive, tmp_path)

    result = destination.check_integrity(_make_workspace(), str(tmp_path), True)

    assert result == {"files": {}, "extra": []}


def test_nothing_to_check_when_root_folder_creation_failed(drive, tmp_path):
    """If the root folder could not be created, nothing was sent to Drive."""
    drive.fail_root = True
    destination = DriveDestinationBackend()
    workspace = _make_workspace()

    with pytest.raises(HTTPError):
        destination.export(workspace, _user(), str(tmp_path))

    assert destination.check_integrity(workspace, str(tmp_path), False) == {
        "files": {},
        "extra": [],
    }
