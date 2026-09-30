"""Tests for the migration integrity check."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django_celery_results.models import TaskResult

from core.backends.source import SourceFile, SourceFolder, truncate_folder_files
from core.factories import UserFactory, WorkspaceFactory
from core.models import ExtraTaskInfo, FeatureFlag, Workspace
from core.processing.integrity import (
    IntegrityTracker,
    build_integrity_report,
    has_unexplained_discrepancy,
    should_track_file_integrity,
    snapshot_source_files,
)


def _set_feature_flag(is_active):
    FeatureFlag.objects.create(
        name=FeatureFlag.Name.FILE_INTEGRITY_TRACKING, is_active=is_active
    )


# ---------------------------------------------------------------------------
# should_track_file_integrity
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_should_track_defaults_to_off_without_feature_flag_row():
    """No workspace override and no feature flag row: tracking is off."""
    assert should_track_file_integrity(Workspace()) is False


@pytest.mark.django_db
@pytest.mark.parametrize("is_active", [True, False])
def test_should_track_follows_feature_flag_when_workspace_unset(is_active):
    """No workspace override: tracking follows the feature flag."""
    _set_feature_flag(is_active)

    assert should_track_file_integrity(Workspace()) is is_active


@pytest.mark.django_db
@pytest.mark.parametrize("is_active", [True, False])
def test_should_track_workspace_true_overrides_feature_flag(is_active):
    """Workspace explicitly enabled: tracking is on whatever the feature flag."""
    _set_feature_flag(is_active)

    workspace = Workspace(is_file_integrity_tracked=True)

    assert should_track_file_integrity(workspace) is True


@pytest.mark.django_db
@pytest.mark.parametrize("is_active", [True, False])
def test_should_track_workspace_false_overrides_feature_flag(is_active):
    """Workspace explicitly disabled: tracking is off whatever the feature flag."""
    _set_feature_flag(is_active)

    workspace = Workspace(is_file_integrity_tracked=False)

    assert should_track_file_integrity(workspace) is False


def test_workspace_integrity_tracking_defaults_to_unset():
    """A new workspace follows the feature flag by default."""
    assert Workspace().is_file_integrity_tracked is None


# ---------------------------------------------------------------------------
# snapshot_source_files
# ---------------------------------------------------------------------------


def _source_file(file_id, name, extension=".txt"):
    return SourceFile(id=file_id, name=name, extension=extension, download_url="x")


def test_snapshot_lists_every_source_file_with_its_original_path():
    """The snapshot keeps original names; the root folder is not part of the path."""
    folder = SourceFolder(
        name="root",
        files=[_source_file("f1", "readme")],
        children=[
            SourceFolder(
                name="a/b",
                files=[_source_file("f2", "doc", ".pdf")],
                children=[SourceFolder(name="c", files=[_source_file("f3", "x")])],
            )
        ],
    )

    assert snapshot_source_files(folder) == [
        {"source_id": "f1", "source_path": "readme.txt"},
        {"source_id": "f2", "source_path": os.path.join("a/b", "doc.pdf")},
        {"source_id": "f3", "source_path": os.path.join("a/b", "c", "x.txt")},
    ]


# ---------------------------------------------------------------------------
# build_integrity_report
# ---------------------------------------------------------------------------


def _creator(written_files=None, failed_files=None, not_written_ids=None):
    return SimpleNamespace(
        written_files=written_files or {},
        failed_files=failed_files or [],
        not_written_ids=not_written_ids or set(),
    )


def _write(path, size=3):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)


def _drive_ok(item_id="item-1"):
    return {
        "item_id": item_id,
        "title": "a.txt",
        "upload_state": "ready",
        "size": 3,
        "stage": "ok",
    }


SOURCE_A = {"source_id": "f1", "source_path": "a.txt"}


def _build(tmp_path, source_files, creator, kept_source_ids=None, **kwargs):
    return build_integrity_report(
        source_files=source_files,
        kept_source_ids=(
            kept_source_ids
            if kept_source_ids is not None
            else {f["source_id"] for f in source_files}
        ),
        creator=creator,
        local_folder_path=str(tmp_path),
        destination_results=kwargs.get("destination_results", {}),
    )


def test_report_merges_retrieval_and_destination_for_each_file(tmp_path):
    """A file downloaded and found ready on Drive gives one complete ok row."""
    _write(tmp_path / "a.txt")

    report = _build(
        tmp_path,
        [SOURCE_A],
        _creator(written_files={"f1": "a.txt"}),
        destination_results={"drive": {"files": {"a.txt": _drive_ok()}, "extra": []}},
    )

    assert report["files"] == [
        {
            "source_id": "f1",
            "source_path": "a.txt",
            "local_path": "a.txt",
            "local_size": 3,
            "destination": "drive",
            "item_id": "item-1",
            "title": "a.txt",
            "upload_state": "ready",
            "size": 3,
            "stage": "ok",
        }
    ]
    assert report["summary"] == {
        "source_files_count": 1,
        "local_files_count": 1,
        "migrated_files_count": 1,
        "sent_files_count": 1,
        "stages": {"ok": 1},
        "destinations_checked": ["drive"],
        "destinations_not_checked": [],
    }


def test_report_truncated_file_is_explained(tmp_path):
    """A file dropped by the file limit is truncated, not an anomaly."""
    report = _build(tmp_path, [SOURCE_A], _creator(), kept_source_ids=set())

    assert report["files"] == [
        {"source_id": "f1", "source_path": "a.txt", "stage": "truncated"}
    ]


def test_report_download_failure_is_explained(tmp_path):
    """A download that raised is download_failed with its error, not an anomaly."""
    creator = _creator(
        failed_files=[{"id": "f1", "name": "a.txt", "path": "a.txt", "error": "403"}]
    )

    report = _build(tmp_path, [SOURCE_A], creator)

    assert report["files"] == [
        {
            "source_id": "f1",
            "source_path": "a.txt",
            "local_path": "a.txt",
            "stage": "download_failed",
            "error": "403",
        }
    ]


def test_report_file_not_written_is_not_blamed_on_drive(tmp_path):
    """A silent retrieval loss is not_written, with no destination row."""
    creator = _creator(
        failed_files=[{"id": "f1", "name": "a.txt", "path": "a.txt", "error": "none"}],
        not_written_ids={"f1"},
    )

    report = _build(
        tmp_path,
        [SOURCE_A],
        creator,
        destination_results={"drive": {"files": {}, "extra": []}},
    )

    assert [row["stage"] for row in report["files"]] == ["not_written"]
    assert "destination" not in report["files"][0]


def test_report_file_never_downloaded_is_not_attempted(tmp_path):
    """The run stopped before this file was downloaded: explained by the failure."""
    report = _build(tmp_path, [SOURCE_A], _creator())

    assert report["files"] == [
        {"source_id": "f1", "source_path": "a.txt", "stage": "not_attempted"}
    ]


def test_report_file_never_sent_to_destination_is_not_attempted(tmp_path):
    """On disk but absent from the destination result: the export stopped before it."""
    _write(tmp_path / "a.txt")

    report = _build(
        tmp_path,
        [SOURCE_A],
        _creator(written_files={"f1": "a.txt"}),
        destination_results={"drive": {"files": {}, "extra": []}},
    )

    row = report["files"][0]
    assert row["destination"] == "drive"
    assert row["stage"] == "not_attempted"


def test_report_file_is_ok_when_no_destination_checks_files(tmp_path):
    """Without per-file destination, a downloaded file is ok at retrieval level."""
    _write(tmp_path / "a.txt")

    report = _build(
        tmp_path,
        [SOURCE_A],
        _creator(written_files={"f1": "a.txt"}),
        destination_results={"archive": None},
    )

    assert report["files"] == [
        {
            "source_id": "f1",
            "source_path": "a.txt",
            "local_path": "a.txt",
            "local_size": 3,
            "stage": "ok",
        }
    ]
    assert report["summary"]["destinations_not_checked"] == ["archive"]


def test_report_lists_destination_items_without_source_as_extra(tmp_path):
    """Generated files and unknown destination items are extra, not anomalies."""
    users_csv = {**_drive_ok("csv-id"), "generated": True}
    unknown = {"item_id": "dup", "path": "a (1).txt", "upload_state": "ready"}

    report = _build(
        tmp_path,
        [],
        _creator(),
        destination_results={
            "drive": {
                "files": {"users_list_by_migrator.csv": users_csv},
                "extra": [unknown],
            }
        },
    )

    assert report["files"] == [
        {
            "local_path": "users_list_by_migrator.csv",
            "destination": "drive",
            "item_id": "csv-id",
            "title": "a.txt",
            "upload_state": "ready",
            "size": 3,
            "generated": True,
            "stage": "extra_on_destination",
        },
        {
            "destination": "drive",
            "item_id": "dup",
            "path": "a (1).txt",
            "upload_state": "ready",
            "stage": "extra_on_destination",
        },
    ]


def test_report_counts_source_files_ok_on_every_checked_destination(tmp_path):
    """A source file is migrated when all its rows are ok; extra rows don't count."""
    for name in ("a.txt", "b.txt", "c.txt"):
        _write(tmp_path / name)
    source_files = [
        {"source_id": f"f{i}", "source_path": name}
        for i, name in enumerate(("a.txt", "b.txt", "c.txt", "d.txt"))
    ]
    creator = _creator(written_files={"f0": "a.txt", "f1": "b.txt", "f2": "c.txt"})

    report = _build(
        tmp_path,
        source_files,
        creator,
        kept_source_ids={"f0", "f1", "f2"},
        destination_results={
            "drive": {
                "files": {
                    "a.txt": _drive_ok("a"),
                    "b.txt": _drive_ok("b"),
                    "c.txt": {"item_id": "c", "stage": "analysis_unfinished"},
                    "users_list_by_migrator.csv": {
                        **_drive_ok("csv"),
                        "generated": True,
                    },
                },
                "extra": [],
            },
            "other": {
                "files": {
                    "a.txt": _drive_ok("a2"),
                    "b.txt": {"item_id": "b2", "stage": "not_ready"},
                    "c.txt": _drive_ok("c2"),
                },
                "extra": [],
            },
        },
    )

    assert report["summary"]["source_files_count"] == 4
    assert report["summary"]["migrated_files_count"] == 1


def test_report_counts_retrieved_files_as_migrated_without_checked_destination(
    tmp_path,
):
    """Without per-file destination check, a file on disk counts as migrated."""
    _write(tmp_path / "a.txt")

    report = _build(
        tmp_path,
        [SOURCE_A],
        _creator(written_files={"f1": "a.txt"}),
        destination_results={"archive": None},
    )

    assert report["summary"]["migrated_files_count"] == 1
    assert report["summary"]["sent_files_count"] is None


SENT_STAGES = ["ok", "analysis_unfinished", "pending", "not_ready", "size_mismatch"]
NOT_SENT_STAGES = ["not_created", "not_attempted", "unverified"]


def test_report_counts_files_received_by_the_destination_as_sent(tmp_path):
    """A file is sent when the destination holds an item for it, whatever its
    state; failed creations, untried files and unverifiable ones are not."""
    stages = SENT_STAGES + NOT_SENT_STAGES
    names = [f"{stage}.txt" for stage in stages]
    for name in names:
        _write(tmp_path / name)
    source_files = [
        {"source_id": f"f{i}", "source_path": name} for i, name in enumerate(names)
    ]
    source_files.append({"source_id": "lost", "source_path": "lost.txt"})
    creator = _creator(
        written_files={f"f{i}": name for i, name in enumerate(names)},
        failed_files=[
            {"id": "lost", "name": "lost.txt", "path": "lost.txt", "error": "x"}
        ],
    )
    drive_files = {
        name: {"item_id": name, "stage": stage}
        for name, stage in zip(names, stages)
        if stage != "not_attempted"
    }

    report = _build(
        tmp_path,
        source_files,
        creator,
        destination_results={"drive": {"files": drive_files, "extra": []}},
    )

    assert report["summary"]["source_files_count"] == len(stages) + 1
    assert report["summary"]["sent_files_count"] == len(SENT_STAGES)


def test_report_keeps_destination_error_in_summary(tmp_path):
    """A destination that could not be re-read keeps its error in the summary."""
    report = _build(
        tmp_path,
        [],
        _creator(),
        destination_results={"drive": {"files": {}, "extra": [], "error": "boom"}},
    )

    assert report["summary"]["errors"] == {"drive": "boom"}


def test_report_only_checks_destinations_that_return_a_result(tmp_path):
    """Destinations without per-file check (None) add no row and are listed apart."""
    _write(tmp_path / "a.txt")

    report = _build(
        tmp_path,
        [SOURCE_A],
        _creator(written_files={"f1": "a.txt"}),
        destination_results={
            "drive": {"files": {"a.txt": _drive_ok()}, "extra": []},
            "resana": None,
        },
    )

    assert [row["destination"] for row in report["files"]] == ["drive"]
    assert report["summary"]["destinations_checked"] == ["drive"]
    assert report["summary"]["destinations_not_checked"] == ["resana"]


# ---------------------------------------------------------------------------
# has_unexplained_discrepancy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("run_failed", [False, True])
@pytest.mark.parametrize(
    "stage", ["not_written", "not_ready", "size_mismatch", "unverified"]
)
def test_silent_losses_are_always_unexplained(stage, run_failed):
    """Losses nothing else reports are anomalies, even in a failed run."""
    assert has_unexplained_discrepancy({stage: 1}, run_failed=run_failed) is True


@pytest.mark.parametrize("run_failed,expected", [(False, True), (True, False)])
@pytest.mark.parametrize("stage", ["not_attempted", "not_created", "pending"])
def test_interruption_losses_are_unexplained_only_in_successful_run(
    stage, run_failed, expected
):
    """A failed run explains the files it did not finish; a successful one can't."""
    assert has_unexplained_discrepancy({stage: 1}, run_failed=run_failed) is expected


@pytest.mark.parametrize("run_failed", [False, True])
@pytest.mark.parametrize(
    "stage",
    [
        "ok",
        "truncated",
        "download_failed",
        "analysis_unfinished",
        "extra_on_destination",
    ],
)
def test_explained_stages_are_never_unexplained(stage, run_failed):
    """Truncation, known download errors, extra items and unfinished analyses."""
    assert has_unexplained_discrepancy({stage: 1}, run_failed=run_failed) is False


# ---------------------------------------------------------------------------
# IntegrityTracker
# ---------------------------------------------------------------------------


def _extra_task(workspace, task_id="task-1"):
    task_result = TaskResult.objects.create(task_id=task_id)
    return ExtraTaskInfo.objects.create(
        task_result=task_result, workspace=workspace, user=UserFactory()
    )


def _destination(name, result=None, not_implemented=False):
    destination = MagicMock()
    destination.name = name
    if not_implemented:
        destination.check_integrity.side_effect = NotImplementedError
    else:
        destination.check_integrity.return_value = result
    return destination


def _tracker(workspace, tmp_path, folder=None, creator=None, destinations=()):
    folder = folder or SourceFolder(name="root", files=[_source_file("f1", "a")])
    tracker = IntegrityTracker(workspace, folder)
    tracker.set_kept_files(folder)
    tracker.set_retrieval(
        creator or _creator(written_files={"f1": "a.txt"}), str(tmp_path)
    )
    for destination in destinations:
        tracker.add_exported_destination(destination)
    return tracker


@pytest.mark.django_db
def test_tracker_does_nothing_when_tracking_is_disabled(tmp_path):
    """Without tracking, no destination is re-read and nothing is saved."""
    workspace = WorkspaceFactory(is_file_integrity_tracked=False)
    extra_task = _extra_task(workspace)
    drive = _destination("drive", {"files": {}, "extra": []})

    _tracker(workspace, tmp_path, destinations=[drive]).save("task-1", run_failed=False)

    drive.check_integrity.assert_not_called()
    extra_task.refresh_from_db()
    assert extra_task.integrity_report == {}
    assert extra_task.integrity_check_passed is None


@pytest.mark.django_db
def test_tracker_saves_report_on_the_run_extra_task(tmp_path):
    """The report and its flag are saved on the ExtraTaskInfo of the task."""
    _write(tmp_path / "a.txt")
    workspace = WorkspaceFactory(is_file_integrity_tracked=True)
    extra_task = _extra_task(workspace)
    drive = _destination("drive", {"files": {"a.txt": _drive_ok()}, "extra": []})

    _tracker(workspace, tmp_path, destinations=[drive]).save("task-1", run_failed=False)

    drive.check_integrity.assert_called_once_with(
        workspace, str(tmp_path), wait_for_analysis=True
    )
    extra_task.refresh_from_db()
    assert extra_task.integrity_report["summary"]["stages"] == {"ok": 1}
    assert extra_task.integrity_report["files"][0]["item_id"] == "item-1"
    assert extra_task.integrity_check_passed is True


@pytest.mark.django_db
def test_tracker_does_not_wait_for_analysis_after_a_failed_run(tmp_path):
    """A failed run must not be delayed by the malware analysis polling."""
    workspace = WorkspaceFactory(is_file_integrity_tracked=True)
    _extra_task(workspace)
    drive = _destination("drive", {"files": {}, "extra": []})

    _tracker(workspace, tmp_path, destinations=[drive]).save("task-1", run_failed=True)

    drive.check_integrity.assert_called_once_with(
        workspace, str(tmp_path), wait_for_analysis=False
    )


@pytest.mark.django_db
def test_tracker_lists_destination_without_per_file_check_as_not_checked(tmp_path):
    """A destination without check_integrity() is reported as not checked."""
    workspace = WorkspaceFactory(is_file_integrity_tracked=True)
    extra_task = _extra_task(workspace)
    resana = _destination("resana", not_implemented=True)

    _tracker(workspace, tmp_path, destinations=[resana]).save(
        "task-1", run_failed=False
    )

    extra_task.refresh_from_db()
    assert extra_task.integrity_report["summary"]["destinations_not_checked"] == [
        "resana"
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("run_failed,expected", [(False, False), (True, True)])
def test_tracker_not_attempted_is_unexplained_only_in_successful_run(
    tmp_path, run_failed, expected
):
    """A file never sent is explained by a failed run, an anomaly otherwise."""
    _write(tmp_path / "a.txt")
    workspace = WorkspaceFactory(is_file_integrity_tracked=True)
    extra_task = _extra_task(workspace)
    drive = _destination("drive", {"files": {}, "extra": []})

    _tracker(workspace, tmp_path, destinations=[drive]).save(
        "task-1", run_failed=run_failed
    )

    extra_task.refresh_from_db()
    assert extra_task.integrity_report["files"][0]["stage"] == "not_attempted"
    assert extra_task.integrity_check_passed is expected


@pytest.mark.django_db
def test_tracker_marks_files_removed_by_truncation(tmp_path):
    """Files present in the source but dropped by the file limit are truncated."""
    workspace = WorkspaceFactory(is_file_integrity_tracked=True)
    extra_task = _extra_task(workspace)
    folder = SourceFolder(
        name="root", files=[_source_file("f1", "a"), _source_file("f2", "b")]
    )
    tracker = IntegrityTracker(workspace, folder)
    truncate_folder_files(folder, 1)
    tracker.set_kept_files(folder)
    tracker.set_retrieval(_creator(written_files={"f1": "a.txt"}), str(tmp_path))

    tracker.save("task-1", run_failed=False)

    extra_task.refresh_from_db()
    stages = {
        row["source_id"]: row["stage"] for row in extra_task.integrity_report["files"]
    }
    assert stages == {"f1": "ok", "f2": "truncated"}


@pytest.mark.django_db
def test_tracker_reports_files_not_reached_when_retrieval_did_not_start():
    """If the run failed before any download, every source file is not_attempted."""
    workspace = WorkspaceFactory(is_file_integrity_tracked=True)
    extra_task = _extra_task(workspace)
    folder = SourceFolder(name="root", files=[_source_file("f1", "a")])
    tracker = IntegrityTracker(workspace, folder)

    tracker.save("task-1", run_failed=True)

    extra_task.refresh_from_db()
    assert [row["stage"] for row in extra_task.integrity_report["files"]] == [
        "not_attempted"
    ]


@pytest.mark.django_db
def test_tracker_never_raises(tmp_path):
    """An error while building the report is logged, never propagated."""
    workspace = WorkspaceFactory(is_file_integrity_tracked=True)
    extra_task = _extra_task(workspace)
    drive = _destination("drive")
    drive.check_integrity.side_effect = RuntimeError("boom")

    _tracker(workspace, tmp_path, destinations=[drive]).save("task-1", run_failed=False)

    extra_task.refresh_from_db()
    assert extra_task.integrity_check_passed is None


@pytest.mark.django_db
def test_tracker_without_extra_task_does_not_raise(tmp_path):
    """No ExtraTaskInfo for the task (e.g. eager run): the report is skipped."""
    workspace = WorkspaceFactory(is_file_integrity_tracked=True)

    _tracker(workspace, tmp_path).save("unknown-task", run_failed=False)
