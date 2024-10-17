import os
import shutil

from django.conf import settings
from django.utils.module_loading import import_string

from celery.utils.log import get_task_logger

from core.models import Workspace
from core.osmose.osmose_backend import OsmoseFolder
from core.utils import get_dir_size, sizeof_fmt

logger = get_task_logger(__name__)


class FolderCreator:
    def __init__(self):
        self.files_count = None
        self.files_success = 0
        self.files_current = 0
        self.workspace = None

    def __get_files_count(self, folder: OsmoseFolder):
        count = len(folder.files)
        for child in folder.children:
            count += self.__get_files_count(child)
        return count

    def create_folder(self, workspace: Workspace, folder: OsmoseFolder):
        self.workspace = workspace
        self.files_count = self.__get_files_count(folder)
        self.delete_folder(workspace)

        path = self.get_workspace_path(workspace)
        if not os.path.exists(path):
            os.mkdir(path)

        # Do not create folder for the root folder, it is virtual.
        for child in folder.children:
            self.__create_folder(path, workspace, child)

    def get_workspace_path(self, workspace):
        return f"{settings.APP_WORK_DIR}/workspace_{workspace.id}"

    def delete_folder(self, workspace):
        path = self.get_workspace_path(workspace)
        if os.path.exists(path):
            shutil.rmtree(path)

    def __create_folder(
        self, current_dir: str, workspace: Workspace, folder: OsmoseFolder
    ):
        path = os.path.join(current_dir, folder.name)
        if not os.path.exists(path):
            os.mkdir(path)

        for child in folder.children:
            self.__create_folder(path, workspace, child)

        self.__download_folder_files(folder, path)

    def __download_folder_files(self, folder: OsmoseFolder, path: str):
        """
        Download all files in the folder, this is a temporary implementation. It will be replaced via a mount point.

        :param folder:
        :param path:
        :return:
        """

        backend_class = import_string(settings.OSMOSE_BACKEND)
        backend = backend_class()

        for file in folder.files:
            download_url = os.path.join(
                settings.OSMOSE_BASE_ENDPOINT, file.raw_data["downloadUrl"]
            )
            destination = os.path.join(
                path, file.name + os.path.splitext(file.raw_data["originalFilename"])[1]
            )

            self.files_current += 1
            logger.info(f"Downloading file {self.files_current}/{self.files_count} ...")
            backend.download_file(download_url, destination)
            size = get_dir_size(self.get_workspace_path(self.workspace))
            size_formatted = sizeof_fmt(size)
            logger.info(f"Directory size: {size_formatted} ({size})")
            self.files_success += 1
