import os
import shutil
from typing import Callable

from django.conf import settings

from celery.utils.log import get_task_logger

from core.backends.source import AbstractSourceBackend, SourceTokenExpired
from core.models import MigrationFile, MigrationRun, Workspace
from core.processing.migration_run import download_migration_file
from core.utils import sizeof_fmt

logger = get_task_logger(__name__)


class FolderCreator:
    def __init__(self):
        self.files_count = None
        self.files_success = 0
        self.failed_files = []
        self.bytes_written = 0
        self.workspace = None

    def create_folder(
        self,
        workspace: Workspace,
        run: MigrationRun,
        source_backend: AbstractSourceBackend,
        after_download: Callable[[MigrationFile, str], None] | None = None,
    ) -> str:
        """
        Download every file of the run into the local folder. after_download, if
        given, gets each downloaded file with its local path.
        """
        self.workspace = workspace
        self.files_count = run.files.count()
        self.delete_folder(workspace)

        path = self.get_workspace_path(workspace)
        os.makedirs(path, exist_ok=True)
        for directory in run.dirs:
            os.makedirs(os.path.join(path, directory), exist_ok=True)

        for index, migration_file in enumerate(run.files.iterator(), start=1):
            if migration_file.status != MigrationFile.Status.PENDING:
                # Refused while planning: nothing to download.
                self.__add_failed_file(migration_file)
                continue
            logger.info("Downloading file %s/%s ...", index, self.files_count)
            local_path = os.path.join(path, migration_file.path)
            self.__download_file(migration_file, local_path, source_backend)
            if migration_file.status != MigrationFile.Status.DOWNLOADED:
                continue
            if after_download is not None:
                after_download(migration_file, local_path)

        return path

    def get_workspace_path(self, workspace):
        return f"{settings.APP_WORK_DIR}/workspace_{workspace.id}"

    def delete_folder(self, workspace):
        path = self.get_workspace_path(workspace)
        if os.path.exists(path):
            shutil.rmtree(path)

    def __download_file(
        self,
        migration_file: MigrationFile,
        local_path: str,
        source_backend: AbstractSourceBackend,
    ):
        try:
            download_migration_file(migration_file, source_backend, local_path)
        except SourceTokenExpired:
            # The run stops, so the user is asked to log in again.
            self.__add_failed_file(migration_file)
            raise
        if migration_file.status != MigrationFile.Status.DOWNLOADED:
            self.__add_failed_file(migration_file)
            return

        self.bytes_written += migration_file.size
        logger.info(
            "Directory size: %s (%s)",
            sizeof_fmt(self.bytes_written),
            self.bytes_written,
        )
        self.files_success += 1

    def __add_failed_file(self, migration_file: MigrationFile):
        source = migration_file.source
        self.failed_files.append(
            {
                "id": source["id"],
                "name": source["name"] + source["extension"],
                "path": migration_file.path,
                "error": migration_file.error,
            }
        )
