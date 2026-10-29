"""ResanaDestinationBackend — wraps ResanaBackend and implements AbstractDestinationBackend."""

import csv
import io

from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import BotoCoreError, ClientError
from celery.utils.log import get_task_logger

from core.backends.destination import (
    AbstractDestinationBackend,
    FileExportStatus,
    RejectedFile,
)
from core.destinations.resana.paths import resana_dir_renames, resana_path
from core.destinations.resana.resana_backend import ResanaBackend
from core.destinations.resana.s3_resana_manager import S3ResanaManager
from core.models import MigrationRun, Workspace
from core.processing.rejected_files import RejectedFilesArchive

logger = get_task_logger(__name__)

# Members listing Resana imports along with the files.
_MEMBERS_CSV = "osmose_users.csv"
_UPLOAD_FAILED = "upload_failed"


class ResanaDestinationBackend(AbstractDestinationBackend):
    """
    Destination backend that uploads the workspace to Resana via an async import job.

    Each file of the run goes to the bucket Resana imports from. Once every file
    is there, the Resana workspace and its import job are created: the
    destination status stays PENDING until the refresh_job management command
    sees the job finished.
    """

    name = "resana"
    label = "Resana"
    exports_file_by_file = True

    def export(self, workspace, user, local_folder_path: str) -> None:
        """Resana receives the run file by file, see exports_file_by_file."""
        raise NotImplementedError

    def describe_rejection(self, code: str) -> str:
        if code == _UPLOAD_FAILED:
            # pylint: disable-next=fixme
            # TODO: French only until the backend ships translations (.po).
            return "Fichier non envoyé vers Resana (erreur d'envoi)"
        return super().describe_rejection(code)

    # --- File-by-file export ---

    def open_export(self, user, run) -> None:
        workspace = run.workspace
        state = ResanaBackend().prepare_import(workspace, user)
        manager = S3ResanaManager()
        prefix = manager.get_destination_path(workspace)
        # Resana would import the files a former run left there.
        manager.delete_prefix(prefix)
        self._save_state(
            run, {**state, "prefix": prefix, "renames": resana_dir_renames(run.dirs)}
        )

    def put_file(self, user, run, migration_file, local_path: str) -> None:
        state = run.destinations_state[self.name]
        s3_key = (
            f"{state['prefix']}/{resana_path(migration_file.path, state['renames'])}"
        )
        try:
            S3ResanaManager().upload_file(local_path, s3_key)
        except (BotoCoreError, ClientError, S3UploadFailedError) as error:
            migration_file.results[self.name] = {
                "status": FileExportStatus.FAILED,
                "error": str(error),
            }
            return
        migration_file.results[self.name] = {"status": FileExportStatus.SENT}

    def close_export(self, user, run) -> None:
        workspace = run.workspace
        state = run.destinations_state[self.name]

        sent, lost = 0, []
        for row in run.files.iterator():
            result = row.results.get(self.name)
            if result is None:
                continue
            if result["status"] == FileExportStatus.SENT:
                sent += 1
            else:
                lost.append(RejectedFile(row.path, _UPLOAD_FAILED))
        RejectedFilesArchive(workspace, self).build(run, lost)
        if lost:
            # One event per run, without paths: file names may be personal data.
            logger.error("%s file(s) not sent to Resana", len(lost))
            if not sent:
                raise RuntimeError(
                    f"All {len(lost)} file(s) failed to be sent to Resana "
                    f"for workspace {workspace.id}"
                )

        if workspace.members:
            self._put_members_csv(workspace, state["prefix"])
        ResanaBackend().create_import(
            workspace, state["user_uuid"], state["organization_uuid"], state["prefix"]
        )
        workspace.set_destination_status(self.name, Workspace.Status.PENDING)
        workspace.save()

    def abort_export(self, run) -> None:
        """Delete what the run stored, unless an import job already reads it."""
        state = run.destinations_state.get(self.name)
        if not state:
            return
        if run.workspace.get_destination_metadata(self.name).get("job_id"):
            return
        S3ResanaManager().delete_prefix(state["prefix"])

    def get_error_details(self, workspace) -> list:
        backend = ResanaBackend()
        return backend.get_error_details(workspace)

    def retry(self, workspace) -> None:
        backend = ResanaBackend()
        backend.retry_job(workspace)

    def poll_completion(self, workspace) -> None:
        backend = ResanaBackend()
        backend.refresh_job(workspace)

    def _save_state(self, run, state: dict) -> None:
        run.destinations_state[self.name] = state
        MigrationRun.objects.filter(pk=run.pk).update(
            destinations_state=run.destinations_state
        )

    @staticmethod
    def _put_members_csv(workspace, prefix: str) -> None:
        buffer = io.StringIO()
        csv.writer(buffer).writerows(
            [m["name"], m["firstName"], m["email"]] for m in workspace.members
        )
        S3ResanaManager().put_object(
            f"{prefix}/{_MEMBERS_CSV}", buffer.getvalue().encode("utf-8")
        )
