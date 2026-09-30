"""Per-file integrity check of a migration run (source, local disk, destination)."""

import os
from collections import Counter
from enum import StrEnum
from types import SimpleNamespace

from celery.utils.log import get_task_logger

from core.backends.source import SourceFolder
from core.models import ExtraTaskInfo, FeatureFlag, Workspace

logger = get_task_logger(__name__)


class Stage(StrEnum):
    """Where a file ended up, or the step where it was lost."""

    # Retrieval (source -> local disk)
    TRUNCATED = "truncated"
    DOWNLOAD_FAILED = "download_failed"
    NOT_WRITTEN = "not_written"
    # Transfer (local disk -> destination)
    NOT_ATTEMPTED = "not_attempted"
    NOT_CREATED = "not_created"
    PENDING = "pending"
    NOT_READY = "not_ready"
    # Received with the right size, still under malware analysis: not awaited
    # because the run failed, or still running at the end of the wait.
    ANALYSIS_UNFINISHED = "analysis_unfinished"
    SIZE_MISMATCH = "size_mismatch"
    UNVERIFIED = "unverified"
    EXTRA_ON_DESTINATION = "extra_on_destination"
    OK = "ok"


def should_track_file_integrity(workspace: Workspace) -> bool:
    """The workspace setting wins when set; otherwise follow the feature flag.
    Unlike core.utils.is_feature(), a missing flag row means disabled."""
    if workspace.is_file_integrity_tracked is not None:
        return workspace.is_file_integrity_tracked
    return FeatureFlag.objects.filter(
        name=FeatureFlag.Name.FILE_INTEGRITY_TRACKING, is_active=True
    ).exists()


# Silent losses: nothing else reports them, whatever the outcome of the run.
ALWAYS_UNEXPLAINED_STAGES = {
    Stage.NOT_WRITTEN,
    Stage.NOT_READY,
    Stage.SIZE_MISMATCH,
    Stage.UNVERIFIED,
}
# Files a run did not finish: explained by a failed run (its exception is
# the cause), a migrator bug in a successful one, which goes through every file.
INTERRUPTION_STAGES = {
    Stage.NOT_ATTEMPTED,
    Stage.NOT_CREATED,
    Stage.PENDING,
}


def has_unexplained_discrepancy(stages, run_failed: bool) -> bool:
    """Whether a run lost a file for a reason nothing else accounts for.
    stages is any collection of Stage, e.g. the report summary's counts."""
    unexplained = ALWAYS_UNEXPLAINED_STAGES
    if not run_failed:
        unexplained = unexplained | INTERRUPTION_STAGES
    return any(stage in unexplained for stage in stages)


# Every key a report row can have, in display order.
REPORT_COLUMNS = [
    "stage",
    "destination",
    "source_path",
    "local_path",
    "local_size",
    "item_id",
    "title",
    "path",
    "upload_state",
    "size",
    "generated",
    "error",
    "source_id",
]


def rows_by_severity(rows: list[dict]) -> list[dict]:
    """Unexplained losses first, then explained ones, then ok rows."""

    def severity(row):
        if row["stage"] in ALWAYS_UNEXPLAINED_STAGES | INTERRUPTION_STAGES:
            return 0
        return 2 if row["stage"] == Stage.OK else 1

    return sorted(rows, key=severity)


class IntegrityTracker:
    """Collects what an export run did with each file, then saves the run's
    integrity report on its ExtraTaskInfo. Every method is a no-op when
    tracking is disabled for the workspace."""

    # Stand-in for a FolderCreator when the run failed before downloading.
    _NO_RETRIEVAL = SimpleNamespace(
        written_files={}, failed_files=[], not_written_ids=set()
    )

    def __init__(self, workspace: Workspace, source_folder: SourceFolder):
        self.workspace = workspace
        self.enabled = should_track_file_integrity(workspace)
        self.source_files = snapshot_source_files(source_folder) if self.enabled else []
        self.kept_source_ids = {file["source_id"] for file in self.source_files}
        self.creator = self._NO_RETRIEVAL
        self.local_folder_path = ""
        self.exported_destinations = []

    def set_kept_files(self, truncated_folder: SourceFolder) -> None:
        """Record which source files survived truncate_folder_files()."""
        if self.enabled:
            self.kept_source_ids = {
                file["source_id"] for file in snapshot_source_files(truncated_folder)
            }

    def set_retrieval(self, creator, local_folder_path: str) -> None:
        self.creator = creator
        self.local_folder_path = local_folder_path

    def add_exported_destination(self, destination) -> None:
        self.exported_destinations.append(destination)

    def save(self, task_id: str, run_failed: bool) -> None:
        """Build and save the report. Never raises: the check must neither fail
        a successful migration nor hide the exception of a failed one."""
        if not self.enabled:
            return
        try:
            self._save(task_id, run_failed)
        # Broad on purpose, see the docstring.
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Could not save the integrity report of task %s", task_id)

    def _save(self, task_id: str, run_failed: bool) -> None:
        extra_task = ExtraTaskInfo.objects.filter(task_result__task_id=task_id).first()
        if extra_task is None:
            logger.warning(
                "No ExtraTaskInfo for task %s, integrity report skipped", task_id
            )
            return

        destination_results = {}
        for destination in self.exported_destinations:
            try:
                destination_results[destination.name] = destination.check_integrity(
                    self.workspace,
                    self.local_folder_path,
                    wait_for_analysis=not run_failed,
                )
            except NotImplementedError:
                destination_results[destination.name] = None

        report = build_integrity_report(
            source_files=self.source_files,
            kept_source_ids=self.kept_source_ids,
            creator=self.creator,
            local_folder_path=self.local_folder_path,
            destination_results=destination_results,
        )

        extra_task.integrity_report = report
        extra_task.integrity_check_passed = not has_unexplained_discrepancy(
            report["summary"]["stages"], run_failed=run_failed
        )
        extra_task.save(update_fields=["integrity_report", "integrity_check_passed"])


def snapshot_source_files(folder: SourceFolder) -> list[dict]:
    """List every source file with its original path, root folder excluded.
    Must run before truncate_folder_files(), which mutates the tree."""
    files = []

    def visit(node: SourceFolder, path: str) -> None:
        for file in node.files:
            files.append(
                {
                    "source_id": file.id,
                    "source_path": os.path.join(path, file.name_with_extension),
                }
            )
        for child in node.children:
            visit(child, os.path.join(path, child.name))

    visit(folder, "")
    return files


def build_integrity_report(
    *,
    source_files: list[dict],
    kept_source_ids: set,
    creator,
    local_folder_path: str,
    destination_results: dict[str, dict | None],
) -> dict:
    """Build the per-file listing of a run, one row per file and per checked
    destination. destination_results maps each destination exported by the run
    to its check_integrity() result, or None when it has no per-file check."""
    checked_results = {
        destination: result
        for destination, result in destination_results.items()
        if result is not None
    }
    failed_by_id = {failed["id"]: failed for failed in creator.failed_files}
    matched_local_paths = set()
    rows = []

    for source_file in source_files:
        row = _retrieval_row(
            source_file, kept_source_ids, failed_by_id, creator, local_folder_path
        )
        if "stage" in row:
            rows.append(row)
            continue

        matched_local_paths.add(row["local_path"])
        if not checked_results:
            rows.append({**row, "stage": Stage.OK})
            continue

        for destination, result in checked_results.items():
            entry = result["files"].get(
                row["local_path"], {"stage": Stage.NOT_ATTEMPTED}
            )
            rows.append({**row, "destination": destination, **entry})

    rows.extend(_extra_rows(checked_results, matched_local_paths))

    summary = _summary(
        rows,
        source_files_count=len(source_files),
        local_files_count=len(creator.written_files),
        destination_results=destination_results,
    )
    return {"summary": summary, "files": rows}


# The destination holds an item for the file, usable or not.
SENT_STAGES = {
    Stage.OK,
    Stage.ANALYSIS_UNFINISHED,
    Stage.PENDING,
    Stage.NOT_READY,
    Stage.SIZE_MISMATCH,
}


def _sent_files_count(rows: list[dict]) -> int:
    """Source files received by every checked destination."""
    stages_by_source = {}
    for row in rows:
        if "source_id" in row and "destination" in row:
            stages_by_source.setdefault(row["source_id"], set()).add(row["stage"])
    return sum(1 for stages in stages_by_source.values() if stages <= SENT_STAGES)


def _migrated_files_count(rows: list[dict]) -> int:
    """Source files whose every row (one per checked destination) is ok."""
    stages_by_source = {}
    for row in rows:
        if "source_id" in row:
            stages_by_source.setdefault(row["source_id"], set()).add(row["stage"])
    return sum(1 for stages in stages_by_source.values() if stages == {Stage.OK})


def _summary(
    rows: list[dict],
    *,
    source_files_count: int,
    local_files_count: int,
    destination_results: dict[str, dict | None],
) -> dict:
    summary = {
        "source_files_count": source_files_count,
        "local_files_count": local_files_count,
        "migrated_files_count": _migrated_files_count(rows),
        # Unknown when no destination was checked file by file.
        "sent_files_count": (
            _sent_files_count(rows)
            if any(result is not None for result in destination_results.values())
            else None
        ),
        "stages": dict(Counter(row["stage"] for row in rows)),
        "destinations_checked": [
            destination
            for destination, result in destination_results.items()
            if result is not None
        ],
        "destinations_not_checked": [
            destination
            for destination, result in destination_results.items()
            if result is None
        ],
    }
    errors = {
        destination: result["error"]
        for destination, result in destination_results.items()
        if result is not None and "error" in result
    }
    if errors:
        summary["errors"] = errors
    return summary


def _retrieval_row(
    source_file: dict,
    kept_source_ids: set,
    failed_by_id: dict,
    creator,
    local_folder_path: str,
) -> dict:
    """Row of a source file after retrieval. It has a "stage" when the file was
    lost before reaching the disk, none when it awaits the destination check."""
    source_id = source_file["source_id"]
    row = dict(source_file)

    if source_id not in kept_source_ids:
        return {**row, "stage": Stage.TRUNCATED}

    if source_id in failed_by_id:
        failed = failed_by_id[source_id]
        stage = (
            Stage.NOT_WRITTEN
            if source_id in creator.not_written_ids
            else Stage.DOWNLOAD_FAILED
        )
        return {
            **row,
            "local_path": failed["path"],
            "stage": stage,
            "error": failed["error"],
        }

    local_path = creator.written_files.get(source_id)
    if local_path is None:
        # The run stopped before this file was downloaded.
        return {**row, "stage": Stage.NOT_ATTEMPTED}

    row["local_path"] = local_path
    local_file = os.path.join(local_folder_path, local_path)
    if os.path.isfile(local_file):
        row["local_size"] = os.path.getsize(local_file)
    return row


def _extra_rows(destination_results: dict, matched_local_paths: set) -> list[dict]:
    """Destination items that match no source file (e.g. the generated members CSV)."""
    rows = []
    for destination, result in destination_results.items():
        for local_path, entry in result["files"].items():
            if local_path not in matched_local_paths:
                rows.append(
                    {
                        "local_path": local_path,
                        "destination": destination,
                        **entry,
                        "stage": Stage.EXTRA_ON_DESTINATION,
                    }
                )
        rows.extend(
            {"destination": destination, **item, "stage": Stage.EXTRA_ON_DESTINATION}
            for item in result["extra"]
        )
    return rows
