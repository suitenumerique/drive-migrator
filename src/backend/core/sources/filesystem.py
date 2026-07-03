"""Filesystem source backend — reads workspaces from a local directory tree."""

import csv
import os
import shutil

from django.conf import settings

from core.backends.source import (
    AbstractSourceBackend,
    SourceFile,
    SourceFolder,
    SourceWorkspace,
)

_USERS_CSV = "_users.csv"


class FileSystemSourceBackend(AbstractSourceBackend):
    """
    Source backend that reads workspaces from the local filesystem.
    Useful for development, testing, and migration of already-downloaded archives.

    Directory layout under settings.FILESYSTEM_SOURCE_ROOT:

        <root>/
          <user-email>/
            <workspace>/
              _users.csv         ← optional members (name,firstName,email — no header)
              <files…>

    Only workspaces inside {root}/{user.email}/ are returned.
    If that directory does not exist, an empty list is returned.

    _users.csv is excluded from the file tree and parsed by prepare_export() to
    populate workspace.members.
    """

    source_type = "filesystem"
    label = "Système de fichiers"

    def get_workspaces(self, user) -> list[SourceWorkspace]:
        root = settings.FILESYSTEM_SOURCE_ROOT
        user_dir = os.path.join(root, user.email) if user else None
        if not user_dir or not os.path.isdir(user_dir):
            return []
        workspaces = []
        for entry in os.scandir(user_dir):
            if entry.is_dir():
                workspaces.append(SourceWorkspace(id=entry.path, title=entry.name))
        return workspaces

    def get_workspace_structure(self, workspace) -> SourceFolder:
        return self._build_folder(workspace.source_id)

    def download_file(self, file: SourceFile, destination_path: str) -> None:
        shutil.copy2(file.download_url, destination_path)

    def prepare_export(self, workspace, local_folder_path: str) -> None:
        users_csv = os.path.join(workspace.source_id, _USERS_CSV)
        if not os.path.isfile(users_csv):
            return
        with open(users_csv, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        workspace.members = [
            {"name": row[0], "firstName": row[1], "email": row[2]}
            for row in rows
            if len(row) >= 3 and any(row)
        ]
        workspace.save()

    def _build_folder(self, path: str) -> SourceFolder:
        folder = SourceFolder(name=os.path.basename(path))
        for entry in os.scandir(path):
            if entry.is_dir():
                folder.children.append(self._build_folder(entry.path))
            elif entry.name != _USERS_CSV:
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
