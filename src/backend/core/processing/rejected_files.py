"""Archive of the files a destination refused, for the user to fetch them back."""

import os
import zipfile

from django.conf import settings

from celery.utils.log import get_task_logger

from core.backends.destination import (
    MEMBERS_CSV_FILENAME,
    AbstractDestinationBackend,
    RejectedFile,
)
from core.backends.source import SourceManager
from core.models import Workspace
from core.processing.folder_helper import ArchiveManager

logger = get_task_logger(__name__)

# pylint: disable-next=fixme
# TODO: French only until the backend ships translations (.po).
REJECTED_FILES_CSV_NAME = "fichiers-non-migres.csv"
REJECTED_FILES_S3_KEY = "rejected_files_s3_key"


class RejectedFilesArchive:
    """Record the files a destination refused and push a zip of them to S3.

    To call from the destination export() before its end-of-migration mail, while
    the local folder still holds the files, so the mail can link the zip.
    """

    def __init__(self, workspace: Workspace, destination: AbstractDestinationBackend):
        self.workspace = workspace
        self.destination = destination

    @property
    def s3_key(self) -> str:
        return f"workspace_{self.workspace.id}_rejected_{self.destination.name}.zip"

    @staticmethod
    def clear(workspace: Workspace) -> None:
        """Delete the zips of a previous run, so nothing links them any more.

        A zip S3 failed to delete is only unlinked: the next run of its
        destination overwrites it.
        """
        s3_keys = [
            metadata.pop(REJECTED_FILES_S3_KEY)
            for metadata in workspace.destination_metadata.values()
            if REJECTED_FILES_S3_KEY in metadata
        ]
        if not s3_keys:
            return
        for s3_key in s3_keys:
            try:
                ArchiveManager().delete_file(s3_key)
            # Broad on purpose: S3 being unreachable must not block a new run.
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("Could not delete the rejected files zip %s", s3_key)
        workspace.save(update_fields=["destination_metadata"])

    def build(self, local_folder_path: str, rejected: list[RejectedFile]) -> None:
        # The members listing is the migrator's file, not one of the user's.
        rejected = [
            rejected_file
            for rejected_file in rejected
            if rejected_file.path != MEMBERS_CSV_FILENAME
        ]
        self._record(rejected)
        metadata = dict(self.workspace.get_destination_metadata(self.destination.name))
        metadata.pop(REJECTED_FILES_S3_KEY, None)
        if rejected and self._upload_zip(local_folder_path, rejected):
            metadata[REJECTED_FILES_S3_KEY] = self.s3_key
        self.workspace.set_destination_metadata(self.destination.name, metadata)
        self.workspace.save(update_fields=["upload_errors", "destination_metadata"])

    def get_download_url(self) -> str | None:
        """Short-lived URL of this destination's zip, None when it refused nothing."""
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

    def _upload_zip(self, local_folder_path: str, rejected: list[RejectedFile]) -> bool:
        """Return whether the zip reached S3: the files are on the destination
        already, so failing to archive the refused ones must not fail the run."""
        zip_path = os.path.join(settings.APP_WORK_DIR, self.s3_key)
        try:
            os.makedirs(settings.APP_WORK_DIR, exist_ok=True)
            # Stored, not deflated: refused files are often big and already
            # compressed (pdf, docx, media), compressing them again only costs time.
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zip_file:
                for rejected_file in rejected:
                    local_path = os.path.join(local_folder_path, rejected_file.path)
                    if os.path.isfile(local_path):
                        zip_file.write(local_path, rejected_file.path)
                    else:
                        logger.warning(
                            "Refused file missing locally, left out of the zip"
                        )
                csv_content = self.workspace.get_failed_files_csv(
                    SourceManager().get_backend().label
                )
                zip_file.writestr(REJECTED_FILES_CSV_NAME, csv_content)
            ArchiveManager().upload_file(zip_path, self.s3_key)
        # Broad on purpose: whatever prevents building or storing the zip (S3
        # outage, full disk...), the mail still goes out with the CSV.
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception(
                "Could not archive the files refused by %s for workspace %s",
                self.destination.name,
                self.workspace.id,
            )
            return False
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)
        return True
