import os
import shutil

from django.conf import settings
from django.utils.module_loading import import_string

from celery.utils.log import get_task_logger

from core.models import Workspace
from core.osmose.osmose_backend import OsmoseFolder

logger = get_task_logger(__name__)


class FolderCreator:
    def create_folder(self, workspace: Workspace, folder: OsmoseFolder):
        self.__delete_folder(workspace)

        path = self.get_workspace_path(workspace)
        if not os.path.exists(path):
            os.mkdir(path)

        # Do not create folder for the root folder, it is virtual.
        for child in folder.children:
            self.__create_folder(path, workspace, child)

    def get_workspace_path(self, workspace):
        return f"/tmp/workspace_{workspace.id}"  # noqa: S108

    def __delete_folder(self, workspace):
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

            backend.download_file(download_url, destination)
