"""Filesystem source backend — reads workspaces from a local directory tree."""

import os
import shutil

from django.conf import settings

from core.backends.source import (
    AbstractSourceBackend,
    SourceFile,
    SourceFolder,
    SourceWorkspace,
)


class FileSystemSourceBackend(AbstractSourceBackend):
    """
    Source backend that reads workspaces from the local filesystem.
    Useful for development, testing, and migration of already-downloaded archives.

    Expects settings.FILESYSTEM_SOURCE_ROOT to point to a directory where
    each immediate subdirectory is treated as a separate workspace.
    """

    source_type = "filesystem"

    def get_workspaces(self, user) -> list[SourceWorkspace]:
        root = settings.FILESYSTEM_SOURCE_ROOT
        workspaces = []
        for entry in os.scandir(root):
            if entry.is_dir():
                workspaces.append(SourceWorkspace(id=entry.path, title=entry.name))
        return workspaces

    def get_workspace_structure(self, workspace) -> SourceFolder:
        return self._build_folder(workspace.source_id)

    def download_file(self, file: SourceFile, destination_path: str) -> None:
        shutil.copy2(file.download_url, destination_path)

    def _build_folder(self, path: str) -> SourceFolder:
        folder = SourceFolder(name=os.path.basename(path))
        for entry in os.scandir(path):
            if entry.is_dir():
                folder.children.append(self._build_folder(entry.path))
            else:
                name, ext = os.path.splitext(entry.name)
                folder.files.append(
                    SourceFile(
                        id=entry.path,
                        name=name,
                        extension=ext,
                        download_url=entry.path,
                    )
                )
        return folder
