"""ArchiveDestinationBackend: stores each file on S3, zipped at download time."""

import csv
import io
import json
import posixpath

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import BotoCoreError, ClientError
from celery.utils.log import get_task_logger

from core.backends.destination import (
    MEMBERS_CSV_FILENAME,
    AbstractDestinationBackend,
    FileExportStatus,
    RejectedFile,
)
from core.mails_manager import MailsManager
from core.models import MigrationRun, Workspace
from core.processing.folder_helper import ArchiveManager
from core.processing.rejected_files import RejectedFilesArchive

logger = get_task_logger(__name__)

# Destination metadata key of the S3 manifest listing the archive's content.
MANIFEST_KEY = "manifest_key"
# Destination metadata key of a zip built by the former export.
LEGACY_ZIP_KEY = "s3_key"

_UPLOAD_FAILED = "upload_failed"


class ArchiveDestinationBackend(AbstractDestinationBackend):
    """
    Destination backend storing each file of a run on S3, under opaque keys.

    Once every file is stored, a manifest written next to them maps each key
    to its path in the archive: the zip is streamed from it at download time,
    so it is never built on disk.
    """

    name = "archive"
    label = "Archive ZIP"
    exports_file_by_file = True

    def export(self, workspace, user, local_folder_path: str) -> None:
        """The archive receives the run file by file, see exports_file_by_file."""
        raise NotImplementedError

    def describe_rejection(self, code: str) -> str:
        if code == _UPLOAD_FAILED:
            # pylint: disable-next=fixme
            # TODO: French only until the backend ships translations (.po).
            return "Fichier non enregistré dans l'archive (erreur d'envoi)"
        return super().describe_rejection(code)

    # --- File-by-file export ---

    def open_export(self, user, run) -> None:
        # One prefix per run, so a failed run never touches the previous archive.
        self._save_state(run, {"prefix": f"archives/{run.workspace_id}/{run.id}"})

    def put_file(self, user, run, migration_file, local_path: str) -> None:
        prefix = run.destinations_state[self.name]["prefix"]
        # Ids only: file names may be personal data.
        s3_key = f"{prefix}/files/{migration_file.id}"
        try:
            ArchiveManager().upload_file(local_path, s3_key)
        except (BotoCoreError, ClientError, S3UploadFailedError) as error:
            migration_file.results[self.name] = {
                "status": FileExportStatus.FAILED,
                "error": str(error),
            }
            return
        migration_file.results[self.name] = {
            "status": FileExportStatus.SENT,
            "key": s3_key,
        }

    def close_export(self, user, run) -> None:
        workspace = run.workspace
        prefix = run.destinations_state[self.name]["prefix"]
        manager = ArchiveManager()

        entries, lost = [], []
        for row in run.files.iterator():
            result = row.results.get(self.name)
            if result is None:
                continue
            if result["status"] == FileExportStatus.SENT:
                entries.append(
                    {"path": row.path, "key": result["key"], "size": row.size}
                )
            else:
                lost.append(RejectedFile(row.path, _UPLOAD_FAILED))
        if lost:
            # One event per run, without paths: file names may be personal data.
            logger.error("%s file(s) not stored in the archive", len(lost))
            if not entries:
                self._hand_back(run, lost)
                raise RuntimeError(
                    f"All {len(lost)} file(s) failed to be stored in the archive "
                    f"for workspace {workspace.id}"
                )

        if workspace.members:
            entries.append(self._put_members_csv(manager, workspace, prefix))
        manifest_key = f"{prefix}/manifest.json"
        manager.put_object(
            manifest_key, json.dumps({"dirs": run.dirs, "files": entries}).encode()
        )

        previous = workspace.get_destination_metadata(self.name)
        workspace.set_destination_metadata(self.name, {MANIFEST_KEY: manifest_key})
        # Saves the metadata, after the zip of the refused files is linked.
        self._hand_back(run, lost)
        self._delete_archive(manager, previous)

        title = _(f"Votre archive de l'espace {workspace.title} est prête !")
        download_url = f"{settings.LOGIN_REDIRECT_URL}/download-archive/?workspaceId={workspace.id}"
        MailsManager().send_migration_mail(
            user,
            workspace,
            "archive_download",
            {"title": title, "download_url": download_url},
        )
        workspace.set_destination_status(self.name, Workspace.Status.SUCCESS)
        workspace.save()

    def abort_export(self, run) -> None:
        """Delete what the run stored: a failed export makes no archive."""
        state = run.destinations_state.get(self.name)
        if not state:
            return
        workspace = run.workspace
        workspace.refresh_from_db(fields=["destination_metadata"])
        linked = workspace.get_destination_metadata(self.name).get(MANIFEST_KEY, "")
        if linked.startswith(state["prefix"] + "/"):
            # The close failed after linking it, e.g. on the mail: the previous
            # archive is gone, so this one stays.
            return
        ArchiveManager().delete_prefix(state["prefix"])

    def get_download_url(self, workspace) -> str:
        manager = ArchiveManager()
        return manager.get_download_url(workspace)

    def _save_state(self, run, state: dict) -> None:
        run.destinations_state[self.name] = state
        MigrationRun.objects.filter(pk=run.pk).update(
            destinations_state=run.destinations_state
        )

    def _hand_back(self, run, lost: list[RejectedFile]) -> None:
        """Give the files S3 did not store back to the user."""
        RejectedFilesArchive(run.workspace, self).build(run, lost)

    @staticmethod
    def _put_members_csv(manager, workspace, prefix: str) -> dict:
        """Store the shared-users listing, for the root of the archive."""
        buffer = io.StringIO()
        csv.writer(buffer).writerows(
            [m["name"], m["firstName"], m["email"]] for m in workspace.members
        )
        body = buffer.getvalue().encode("utf-8")
        s3_key = f"{prefix}/{MEMBERS_CSV_FILENAME}"
        manager.put_object(s3_key, body)
        return {"path": MEMBERS_CSV_FILENAME, "key": s3_key, "size": len(body)}

    @staticmethod
    def _delete_archive(manager, metadata: dict) -> None:
        """Delete the archive the metadata links, best effort: once unlinked,
        nothing reaches it any more."""
        try:
            if MANIFEST_KEY in metadata:
                manager.delete_prefix(posixpath.dirname(metadata[MANIFEST_KEY]))
            if LEGACY_ZIP_KEY in metadata:
                manager.delete_file(metadata[LEGACY_ZIP_KEY])
        # Broad on purpose: the new archive is ready whatever S3 answers here.
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Could not delete the previous archive")
