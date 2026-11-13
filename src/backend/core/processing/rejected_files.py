"""Archive of the files a destination refused, for the user to fetch them back."""

import json

from celery.utils.log import get_task_logger

from core.backends.destination import (
    MEMBERS_CSV_FILENAME,
    AbstractDestinationBackend,
    RejectedFile,
)
from core.backends.source import SourceManager
from core.models import MigrationFile, Workspace
from core.processing.folder_helper import ArchiveManager

logger = get_task_logger(__name__)

# pylint: disable-next=fixme
# TODO: French only until the backend ships translations (.po).
REJECTED_FILES_CSV_NAME = "fichiers-non-migres.csv"
# Zip built by the former export, served by S3 itself.
REJECTED_FILES_S3_KEY = "rejected_files_s3_key"
# Manifest of the refused files, whose zip is streamed at download.
REJECTED_FILES_MANIFEST_KEY = "rejected_files_manifest_key"


def _workspace_prefix(workspace_id) -> str:
    return f"rejected/{workspace_id}"


def _run_prefix(run) -> str:
    return f"{_workspace_prefix(run.workspace_id)}/{run.id}"


def has_rejected_files(metadata: dict) -> bool:
    """Whether a destination's metadata links a zip of refused files."""
    return REJECTED_FILES_S3_KEY in metadata or REJECTED_FILES_MANIFEST_KEY in metadata


def keep_refused_file(run, migration_file, local_path: str) -> None:
    """Keep on S3 a file a destination did not take, for the user to get it
    back once the run is over, whichever worker ends it."""
    s3_key = f"{_run_prefix(run)}/files/{migration_file.id}"
    try:
        ArchiveManager().upload_file(local_path, s3_key)
    # Broad on purpose: failing to keep the file only leaves it out of the zip.
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Could not keep a refused file of run %s", run.id)
        return
    migration_file.kept_key = s3_key
    MigrationFile.objects.filter(pk=migration_file.pk).update(kept_key=s3_key)


def drop_unlinked_kept_files(run) -> None:
    """Delete the files the run kept when no destination's zip lists them,
    e.g. the destination that refused them failed afterwards."""
    if not run.files.exclude(kept_key="").exists():
        return
    prefix = _run_prefix(run)
    if any(
        metadata.get(REJECTED_FILES_MANIFEST_KEY, "").startswith(f"{prefix}/")
        for metadata in run.workspace.destination_metadata.values()
    ):
        return
    try:
        ArchiveManager().delete_prefix(prefix)
    # Broad on purpose: called as the run ends, it must not hide how it ended.
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Could not delete the files kept by run %s", run.id)


class RejectedFilesArchive:
    """Record the files a destination refused and list them for their zip.

    To call from the destination close_export() before its end-of-migration
    mail, so the mail can link the zip.
    """

    def __init__(self, workspace: Workspace, destination: AbstractDestinationBackend):
        self.workspace = workspace
        self.destination = destination

    @property
    def manifest_key(self) -> str | None:
        """S3 key of the manifest of this destination's zip, None for a former
        zip or when it refused nothing."""
        return self.workspace.get_destination_metadata(self.destination.name).get(
            REJECTED_FILES_MANIFEST_KEY
        )

    @staticmethod
    def clear(workspace: Workspace) -> None:
        """Delete the zips of a previous run, so nothing links them any more,
        and the files the previous runs kept.

        What S3 failed to delete is only unlinked: the kept files of a run are
        under a prefix of their own, and a former zip is overwritten by the
        next run of its destination.
        """
        legacy_keys, unlinked = [], False
        for metadata in workspace.destination_metadata.values():
            if REJECTED_FILES_S3_KEY in metadata:
                legacy_keys.append(metadata.pop(REJECTED_FILES_S3_KEY))
                unlinked = True
            if metadata.pop(REJECTED_FILES_MANIFEST_KEY, None):
                unlinked = True
        manager = ArchiveManager()
        for s3_key in legacy_keys:
            try:
                manager.delete_file(s3_key)
            # Broad on purpose: S3 being unreachable must not block a new run.
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("Could not delete the rejected files zip %s", s3_key)
        try:
            # Even when nothing links them: a run that stopped midway kept some.
            manager.delete_prefix(_workspace_prefix(workspace.id))
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception(
                "Could not delete the files kept for workspace %s", workspace.id
            )
        if unlinked:
            workspace.save(update_fields=["destination_metadata"])

    def build(self, run, rejected: list[RejectedFile]) -> None:
        # The members listing is the migrator's file, not one of the user's.
        rejected = [
            rejected_file
            for rejected_file in rejected
            if rejected_file.path != MEMBERS_CSV_FILENAME
        ]
        self._record(rejected)
        metadata = dict(self.workspace.get_destination_metadata(self.destination.name))
        metadata.pop(REJECTED_FILES_S3_KEY, None)
        metadata.pop(REJECTED_FILES_MANIFEST_KEY, None)
        if rejected:
            manifest_key = self._put_manifest(run, rejected)
            if manifest_key:
                metadata[REJECTED_FILES_MANIFEST_KEY] = manifest_key
        self.workspace.set_destination_metadata(self.destination.name, metadata)
        self.workspace.save(update_fields=["upload_errors", "destination_metadata"])

    def get_download_url(self) -> str | None:
        """Short-lived URL of this destination's former zip, None otherwise."""
        s3_key = self.workspace.get_destination_metadata(self.destination.name).get(
            REJECTED_FILES_S3_KEY
        )
        if not s3_key:
            return None
        return ArchiveManager().get_presigned_url(s3_key)

    def _record(self, rejected: list[RejectedFile]) -> None:
        """Replace this destination's entries in Workspace.upload_errors."""
        self.workspace.upload_errors = [
            error
            for error in self.workspace.upload_errors
            if error.get("destination") != self.destination.name
        ] + [
            {
                "destination": self.destination.name,
                "path": rejected_file.path,
                "error": rejected_file.code,
                **({"item_id": rejected_file.item_id} if rejected_file.item_id else {}),
            }
            for rejected_file in rejected
        ]

    def _put_manifest(self, run, rejected: list[RejectedFile]) -> str | None:
        """Store the manifest of the zip, the failed files CSV included, and
        return its key, or None when S3 failed: the files are on the
        destination already, so failing to archive the refused ones must not
        fail the run."""
        prefix = f"{_run_prefix(run)}/{self.destination.name}"
        kept = {
            path: (kept_key, size)
            for path, kept_key, size in run.files.filter(
                path__in=[rejected_file.path for rejected_file in rejected]
            )
            .exclude(kept_key="")
            .values_list("path", "kept_key", "size")
        }
        entries = []
        for rejected_file in rejected:
            if rejected_file.path not in kept:
                logger.warning("Refused file not kept on S3, left out of the zip")
                continue
            kept_key, size = kept[rejected_file.path]
            entries.append({"path": rejected_file.path, "key": kept_key, "size": size})
        try:
            manager = ArchiveManager()
            csv_body = self.workspace.get_failed_files_csv(
                SourceManager().get_backend().label
            ).encode("utf-8")
            csv_key = f"{prefix}/{REJECTED_FILES_CSV_NAME}"
            manager.put_object(csv_key, csv_body)
            entries.append(
                {"path": REJECTED_FILES_CSV_NAME, "key": csv_key, "size": len(csv_body)}
            )
            manifest_key = f"{prefix}/manifest.json"
            manager.put_object(
                manifest_key, json.dumps({"dirs": [], "files": entries}).encode()
            )
        # Broad on purpose: whatever prevents storing the manifest, the mail
        # still goes out with the CSV.
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception(
                "Could not archive the files refused by %s for workspace %s",
                self.destination.name,
                self.workspace.id,
            )
            return None
        return manifest_key
