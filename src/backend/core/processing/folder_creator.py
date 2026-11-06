import os
import shutil

from django.conf import settings

from celery.utils.log import get_task_logger

from core.backends.source import AbstractSourceBackend
from core.models import MigrationFile, MigrationRun, Workspace
from core.processing.migration_run import download_migration_file
from core.utils import sizeof_fmt

logger = get_task_logger(__name__)


class FolderCreator:
    def prepare_folder(self, workspace: Workspace, run: MigrationRun) -> str:
        """Create the empty local folder of the run, with its planned folders,
        and return its path."""
        self.delete_folder(workspace)

        path = self.get_workspace_path(workspace)
        os.makedirs(path, exist_ok=True)
        for directory in run.dirs:
            os.makedirs(os.path.join(path, directory), exist_ok=True)
        return path

    def download_file(
        self,
        workspace: Workspace,
        migration_file: MigrationFile,
        source_backend: AbstractSourceBackend,
    ) -> str | None:
        """Download one file of the run into the local folder of the run.
        Return its local path, or None when it did not reach the disk."""
        if migration_file.status in (
            MigrationFile.Status.DOWNLOAD_FAILED,
            MigrationFile.Status.NOT_WRITTEN,
        ):
            # Refused while planning, or failed already: nothing to download.
            # A file downloaded by a task that died is downloaded again.
            return None
        logger.info("Downloading file %s ...", migration_file.position + 1)
        local_path = os.path.join(
            self.get_workspace_path(workspace), migration_file.path
        )
        # The worker that prepared the folder may not be this one.
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        download_migration_file(migration_file, source_backend, local_path)
        if migration_file.status != MigrationFile.Status.DOWNLOADED:
            return None
        logger.info(
            "Downloaded file %s (%s)",
            migration_file.position + 1,
            sizeof_fmt(migration_file.size),
        )
        return local_path

    def get_workspace_path(self, workspace):
        return f"{settings.APP_WORK_DIR}/workspace_{workspace.id}"

    def delete_folder(self, workspace):
        path = self.get_workspace_path(workspace)
        if os.path.exists(path):
            shutil.rmtree(path)
