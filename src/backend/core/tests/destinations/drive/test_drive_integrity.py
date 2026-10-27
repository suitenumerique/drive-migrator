"""Tests for DriveDestinationBackend.check_integrity()."""

# pylint: disable=redefined-outer-name  # pytest fixtures intentionally shadow outer names

import os
from unittest.mock import MagicMock, patch

import pytest
from requests.exceptions import HTTPError

from core.destinations.drive.backend import DriveDestinationBackend

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _drive_settings(settings):
    settings.DRIVE_SHARE_MEMBERS = False
    settings.DRIVE_INTEGRITY_ANALYSIS_TIMEOUT = 30
    settings.DRIVE_INTEGRITY_ANALYSIS_POLL_INTERVAL = 10


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


def _drive_error(code, status_code=400):
    response = MagicMock(status_code=status_code)
    response.json.return_value = {
        "type": "validation_error",
        "errors": [{"code": code, "detail": "Refused.", "attr": None}],
    }
    return HTTPError(f"{status_code} Client Error", response=response)


def _sent_ids(run):
    """Drive item id of each file the run handed to Drive."""
    return {
        row.path: row.results["drive"]["item_id"]
        for row in run.files.all()
        if "drive" in row.results
    }


def _check(run, wait_for_analysis=True):
    return DriveDestinationBackend().check_integrity(
        run.workspace, run, wait_for_analysis
    )


@pytest.fixture()
def migrated(make_run, migrate):
    """Migrate a run, then return it with the Drive id of each of its files."""

    def migrate_run(**run_fields):
        run = make_run(**run_fields)
        migrate(run)
        return run, _sent_ids(run)

    return migrate_run


# ---------------------------------------------------------------------------
# Matching the run's files with Drive items
# ---------------------------------------------------------------------------


def test_ready_file_with_same_size_is_ok(drive_client, migrated):
    """A file found on Drive, ready and with the downloaded size, is ok."""
    run, ids = migrated()
    item_id = ids["report.pdf"]
    drive_client.list_children.return_value = [_drive_file(item_id, "report.pdf")]

    assert _check(run) == {
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


def test_file_renamed_by_drive_is_matched_by_item_id(drive_client, migrated):
    """Drive may rename a duplicate title; matching relies on the item id."""
    run, ids = migrated()
    drive_client.list_children.return_value = [
        _drive_file(ids["report.pdf"], "report (1).pdf")
    ]

    entry = _check(run)["files"]["report.pdf"]

    assert entry["stage"] == "ok"
    assert entry["title"] == "report (1).pdf"


def test_nested_file_is_found_in_its_drive_folder(drive_client, migrated):
    """check_integrity() walks Drive subfolders recursively."""
    path = os.path.join("docs", "a.txt")
    run, ids = migrated(paths=[path])
    drive_client.list_children.side_effect = lambda folder_id: {
        "root-uuid": [_drive_folder("folder-docs", "docs")],
        "folder-docs": [_drive_file(ids[path], "a.txt")],
    }[folder_id]

    assert _check(run)["files"][path]["stage"] == "ok"


def test_size_mismatch_is_reported(drive_client, migrated):
    """A ready file whose Drive size differs from the downloaded one is size_mismatch."""
    run, ids = migrated()
    drive_client.list_children.return_value = [
        _drive_file(ids["report.pdf"], "report.pdf", size=1)
    ]

    assert _check(run)["files"]["report.pdf"]["stage"] == "size_mismatch"


def test_file_not_ready_on_drive_is_reported(drive_client, migrated):
    """A file blocked by the malware analysis is not_ready, raw state kept."""
    run, ids = migrated()
    drive_client.list_children.return_value = [
        _drive_file(ids["report.pdf"], "report.pdf", upload_state="suspicious")
    ]

    entry = _check(run)["files"]["report.pdf"]

    assert entry["stage"] == "not_ready"
    assert entry["upload_state"] == "suspicious"


@pytest.mark.parametrize("drive_size,stage", [(3, "ok"), (1, "size_mismatch")])
def test_file_too_large_to_analyze_is_checked_like_a_ready_file(
    drive_client, migrated, drive_size, stage
):
    """Drive keeps a file too large to analyze downloadable: only its size counts."""
    run, ids = migrated(paths=["video.mp4"])
    drive_client.list_children.return_value = [
        _drive_file(
            ids["video.mp4"],
            "video.mp4",
            upload_state="file_too_large_to_analyze",
            size=drive_size,
        )
    ]

    entry = _check(run)["files"]["video.mp4"]

    assert entry["stage"] == stage
    assert entry["upload_state"] == "file_too_large_to_analyze"


def test_file_missing_from_listing_and_unknown_to_drive_is_not_created(
    drive_client, migrated
):
    """An expected item absent from the listing and 404 on GET is not_created."""
    run, ids = migrated()
    item_id = ids["report.pdf"]

    result = _check(run)

    drive_client.get_item.assert_called_once_with(item_id)
    assert result["files"]["report.pdf"] == {"item_id": item_id, "stage": "not_created"}


def test_file_missing_from_listing_but_pending_on_drive_is_pending(
    drive_client, migrated
):
    """The listing hides pending items: a GET tells a pending item from a missing one."""
    run, ids = migrated()
    drive_client.get_item.return_value = _drive_file(
        ids["report.pdf"], "report.pdf", upload_state="pending", size=None
    )

    entry = _check(run)["files"]["report.pdf"]

    assert entry["stage"] == "pending"
    assert entry["upload_state"] == "pending"


def test_file_rejected_by_drive_carries_the_drive_error(drive_client, migrated):
    """A file Drive refused shows the Drive error code next to its stage."""
    drive_client.notify_upload_ended.side_effect = _drive_error("file_type_not_allowed")
    run, ids = migrated(paths=["page.txt"])

    assert _check(run)["files"]["page.txt"] == {
        "item_id": ids["page.txt"],
        "stage": "not_created",
        "error": "file_type_not_allowed",
    }


def test_drive_file_without_counterpart_in_the_run_is_extra(drive_client, migrated):
    """A Drive file this run did not upload is listed in extra with its Drive path."""
    run, _ = migrated(paths=[])
    drive_client.list_children.side_effect = lambda folder_id: {
        "root-uuid": [_drive_folder("folder-docs", "docs")],
        "folder-docs": [_drive_file("dup-id", "a (1).txt")],
    }[folder_id]

    result = _check(run)

    assert result["files"] == {}
    assert result["extra"] == [
        {
            "item_id": "dup-id",
            "path": os.path.join("docs", "a (1).txt"),
            "upload_state": "ready",
            "size": 3,
        }
    ]


def test_generated_users_csv_is_flagged(drive_client, migrated):
    """users_list_by_migrator.csv is written by the migrator itself, not by the source."""
    run, _ = migrated(
        paths=[], members=[{"name": "A", "firstName": "B", "email": "c@d.fr"}]
    )
    csv_id = drive_client.create_file_item.call_args.kwargs["item_id"]
    drive_client.list_children.return_value = [
        _drive_file(csv_id, "users_list_by_migrator.csv", size=None)
    ]

    entry = _check(run)["files"]["users_list_by_migrator.csv"]

    assert entry["generated"] is True
    assert entry["stage"] == "ok"


# ---------------------------------------------------------------------------
# Waiting for Drive's malware analysis
# ---------------------------------------------------------------------------


def test_analyzing_file_that_becomes_ready_is_ok(drive_client, migrated):
    """Files still analyzing are polled until they settle."""
    run, ids = migrated()
    item_id = ids["report.pdf"]
    drive_client.list_children.return_value = [
        _drive_file(item_id, "report.pdf", upload_state="analyzing")
    ]
    drive_client.get_item.return_value = _drive_file(item_id, "report.pdf")

    with patch("core.destinations.drive.backend.time") as mock_time:
        mock_time.monotonic.return_value = 0
        result = _check(run)

    mock_time.sleep.assert_called_once_with(10)
    drive_client.get_item.assert_called_once_with(item_id)
    assert result["files"]["report.pdf"]["stage"] == "ok"


def test_analyzing_file_after_timeout_is_analysis_unfinished(drive_client, migrated):
    """Polling stops at DRIVE_INTEGRITY_ANALYSIS_TIMEOUT: a slow analysis is not
    a loss, the file is analysis_unfinished."""
    run, ids = migrated()
    analyzing = _drive_file(ids["report.pdf"], "report.pdf", upload_state="analyzing")
    drive_client.list_children.return_value = [analyzing]
    drive_client.get_item.return_value = analyzing

    with patch("core.destinations.drive.backend.time") as mock_time:
        mock_time.monotonic.side_effect = [0, 0, 10, 20, 30]
        result = _check(run)

    assert mock_time.sleep.call_count == 3
    entry = result["files"]["report.pdf"]
    assert entry["stage"] == "analysis_unfinished"
    assert entry["upload_state"] == "analyzing"


def test_no_polling_when_not_waiting_for_analysis(drive_client, migrated):
    """With wait_for_analysis=False (failed run), analyzing files are not polled."""
    run, ids = migrated()
    drive_client.list_children.return_value = [
        _drive_file(ids["report.pdf"], "report.pdf", upload_state="analyzing")
    ]

    with patch("core.destinations.drive.backend.time") as mock_time:
        result = _check(run, wait_for_analysis=False)

    mock_time.sleep.assert_not_called()
    drive_client.get_item.assert_not_called()
    entry = result["files"]["report.pdf"]
    assert entry["stage"] == "analysis_unfinished"
    assert entry["upload_state"] == "analyzing"


@pytest.mark.parametrize("wait_for_analysis", [True, False])
def test_analyzing_file_with_wrong_size_is_size_mismatch(
    drive_client, migrated, wait_for_analysis
):
    """The size is checked even while the malware analysis is unfinished."""
    run, ids = migrated()
    analyzing = _drive_file(
        ids["report.pdf"], "report.pdf", upload_state="analyzing", size=2
    )
    drive_client.list_children.return_value = [analyzing]
    drive_client.get_item.return_value = analyzing

    with patch("core.destinations.drive.backend.time") as mock_time:
        mock_time.monotonic.side_effect = [0, 0, 10, 20, 30]
        result = _check(run, wait_for_analysis=wait_for_analysis)

    assert result["files"]["report.pdf"]["stage"] == "size_mismatch"


def test_blocked_file_is_not_ready_even_without_waiting(drive_client, migrated):
    """Only a file still analyzing depends on the wait: a blocked one is not_ready."""
    run, ids = migrated()
    drive_client.list_children.return_value = [
        _drive_file(ids["report.pdf"], "report.pdf", upload_state="suspicious")
    ]

    assert _check(run, wait_for_analysis=False)["files"]["report.pdf"]["stage"] == (
        "not_ready"
    )


# ---------------------------------------------------------------------------
# Failed files and unreadable Drive
# ---------------------------------------------------------------------------


def test_file_whose_creation_failed_is_checked_with_its_client_id(
    drive_client, migrated
):
    """A file failing at creation is still looked up on Drive, by the id sent."""
    drive_client.create_file_item.side_effect = _drive_error("server_error", 500)
    run, ids = migrated()
    client_id = ids["report.pdf"]

    result = _check(run, wait_for_analysis=False)

    drive_client.get_item.assert_called_once_with(client_id)
    assert result["files"]["report.pdf"] == {
        "item_id": client_id,
        "stage": "not_created",
        "error": str(drive_client.create_file_item.side_effect),
    }


def test_listing_failure_marks_sent_files_unverified(drive_client, migrated):
    """If Drive can't be re-read, sent files are unverified and the error kept."""
    run, ids = migrated()
    drive_client.list_children.side_effect = RuntimeError("token expired")

    assert _check(run) == {
        "files": {
            "report.pdf": {"item_id": ids["report.pdf"], "stage": "unverified"},
        },
        "extra": [],
        "error": "token expired",
    }


def test_listing_failure_keeps_the_drive_error_of_rejected_files(
    drive_client, migrated
):
    """Drive refusals are known locally: an unverified report still shows them."""
    drive_client.notify_upload_ended.side_effect = [
        _drive_error("file_type_not_allowed"),
        None,
    ]
    run, ids = migrated(paths=["page.txt", "report.pdf"])
    drive_client.list_children.side_effect = RuntimeError("token expired")

    assert _check(run)["files"] == {
        "page.txt": {
            "item_id": ids["page.txt"],
            "stage": "unverified",
            "error": "file_type_not_allowed",
        },
        "report.pdf": {"item_id": ids["report.pdf"], "stage": "unverified"},
    }


def test_nothing_to_check_when_drive_did_not_export_the_run(drive_client, make_run):
    """A run Drive never opened has nothing on Drive to compare with."""
    assert _check(make_run()) == {"files": {}, "extra": []}
    drive_client.list_children.assert_not_called()


def test_nothing_to_check_when_root_folder_creation_failed(drive_client, migrated):
    """If the root folder could not be created, nothing was sent to Drive."""
    drive_client.create_folder.side_effect = RuntimeError("Drive down")
    run, _ = migrated()

    assert _check(run) == {"files": {}, "extra": []}


def test_nothing_to_check_without_a_run():
    """The run failed before planning its files: there is nothing to check."""
    workspace = MagicMock()

    assert DriveDestinationBackend().check_integrity(workspace, None, True) == {
        "files": {},
        "extra": [],
    }
