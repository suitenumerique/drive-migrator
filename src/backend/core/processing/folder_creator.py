import os
import shutil

from django.conf import settings

from celery.utils.log import get_task_logger

from core.backends.source import AbstractSourceBackend, SourceFolder
from core.models import Workspace
from core.utils import (
    ensure_file_uniqueness,
    get_dir_size,
    sanitize_path_component,
    sizeof_fmt,
    truncate_path_parts,
)

logger = get_task_logger(__name__)


class FolderCreator:
    def __init__(self):
        self.files_count = None
        self.files_success = 0
        self.files_current = 0
        self.workspace = None

    def __get_files_count(self, folder: SourceFolder):
        count = len(folder.files)
        for child in folder.children:
            count += self.__get_files_count(child)
        return count

    def create_folder(
        self,
        workspace: Workspace,
        folder: SourceFolder,
        source_backend: AbstractSourceBackend,
    ) -> str:
        self.workspace = workspace
        self.files_count = self.__get_files_count(folder)
        self.delete_folder(workspace)

        path = self.get_workspace_path(workspace)
        os.makedirs(path, exist_ok=True)

        for child in folder.children:
            self.__create_folder(path, workspace, child, source_backend)

        self.__download_folder_files(folder, path, source_backend)

        return path

    def get_workspace_path(self, workspace):
        return f"{settings.APP_WORK_DIR}/workspace_{workspace.id}"

    def delete_folder(self, workspace):
        path = self.get_workspace_path(workspace)
        if os.path.exists(path):
            shutil.rmtree(path)

    def __create_folder(
        self,
        current_dir: str,
        workspace: Workspace,
        folder: SourceFolder,
        source_backend: AbstractSourceBackend,
    ):
        path = truncate_path_parts(
            os.path.join(current_dir, sanitize_path_component(folder.name))
        )
        if not os.path.exists(path):
            os.mkdir(path)

        for child in folder.children:
            self.__create_folder(path, workspace, child, source_backend)

        self.__download_folder_files(folder, path, source_backend)

    def __download_folder_files(
        self, folder: SourceFolder, path: str, source_backend: AbstractSourceBackend
    ):
        for file in folder.files:
            self.files_current += 1
            logger.info(
                "Downloading file %s/%s ...", self.files_current, self.files_count
            )

            destination = os.path.join(
                path, sanitize_path_component(file.name_with_extension)
            )
            destination_truncated = truncate_path_parts(destination)
            if destination != destination_truncated:
                logger.info(
                    "Truncated filename: %s into %s", destination, destination_truncated
                )
                destination = destination_truncated

            destination_uniqueness = ensure_file_uniqueness(destination)
            if destination != destination_uniqueness:
                logger.info(
                    "Uniquenessify filename: %s into %s",
                    destination,
                    destination_uniqueness,
                )
                destination = destination_uniqueness

            source_backend.download_file(file, destination)
            size = get_dir_size(self.get_workspace_path(self.workspace))
            size_formatted = sizeof_fmt(size)
            logger.info("Directory size: %s (%s)", size_formatted, size)
            self.files_success += 1
