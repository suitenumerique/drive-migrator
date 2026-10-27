import os
import shutil

from django.conf import settings

from celery.utils.log import get_task_logger

from core.backends.source import (
    AbstractSourceBackend,
    SourceFile,
    SourceFolder,
    SourceTokenExpired,
)
from core.models import Workspace
from core.processing.plan import PlannedFile, build_plan
from core.utils import sizeof_fmt

logger = get_task_logger(__name__)


class FolderCreator:
    def __init__(self):
        self.files_count = None
        self.files_success = 0
        self.failed_files = []
        # Source file id -> path relative to the workspace root, for every file
        # actually written to disk.
        self.written_files = {}
        # Source file ids whose download returned without error but left no file.
        self.not_written_ids = set()
        self.bytes_written = 0
        self.workspace = None

    def create_folder(
        self,
        workspace: Workspace,
        folder: SourceFolder,
        source_backend: AbstractSourceBackend,
    ) -> str:
        self.workspace = workspace
        plan = build_plan(folder)
        self.files_count = len(plan.files)
        self.delete_folder(workspace)

        path = self.get_workspace_path(workspace)
        os.makedirs(path, exist_ok=True)
        for directory in plan.dirs:
            os.makedirs(os.path.join(path, directory), exist_ok=True)

        for refused in plan.refused:
            self.__add_failed_file(refused.source, refused.path, refused.error)

        for index, planned_file in enumerate(plan.files, start=1):
            logger.info("Downloading file %s/%s ...", index, self.files_count)
            self.__download_file(planned_file, path, source_backend)

        return path

    def get_workspace_path(self, workspace):
        return f"{settings.APP_WORK_DIR}/workspace_{workspace.id}"

    def delete_folder(self, workspace):
        path = self.get_workspace_path(workspace)
        if os.path.exists(path):
            shutil.rmtree(path)

    def __download_file(
        self,
        planned_file: PlannedFile,
        root: str,
        source_backend: AbstractSourceBackend,
    ):
        file = planned_file.source
        relative_path = planned_file.path
        destination = os.path.join(root, relative_path)

        try:
            source_backend.download_file(file, destination)
        except SourceTokenExpired as error:
            # Every remaining file would fail the same way: stop the run,
            # so the user is asked to log in again.
            self.__add_failed_file(file, relative_path, str(error))
            raise
        # Broad on purpose: source_backend is backend-agnostic (Resana, Osmose,
        # filesystem...), so we can't target a specific exception type here. One
        # file failing must not abort the rest of the workspace's download.
        except Exception as error:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            logger.error("Failed to download %s: %s", file.name_with_extension, error)
            if os.path.exists(destination):
                os.remove(destination)
            self.__add_failed_file(file, relative_path, str(error))
            return

        if not os.path.isfile(destination):
            logger.error(
                "Download of %s reported success but wrote no file",
                file.name_with_extension,
            )
            self.not_written_ids.add(file.id)
            self.__add_failed_file(
                file, relative_path, "Download reported success but wrote no file"
            )
            return

        self.written_files[file.id] = relative_path

        self.bytes_written += os.path.getsize(destination)
        logger.info(
            "Directory size: %s (%s)",
            sizeof_fmt(self.bytes_written),
            self.bytes_written,
        )
        self.files_success += 1

    def __add_failed_file(self, file: SourceFile, relative_path: str, error: str):
        self.failed_files.append(
            {
                "id": file.id,
                "name": file.name_with_extension,
                "path": relative_path,
                "error": error,
            }
        )
