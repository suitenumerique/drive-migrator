"""ResanaSourceBackend — reads workspaces from the Interstis GED API."""

from core.backends.source import (
    AbstractSourceBackend,
    SourceFile,
    SourceFolder,
    SourceWorkspace,
)
from core.sources.resana.interstis_client import InterstisClient
from core.sources.resana.token_manager import ResanaTokenManager


class ResanaSourceBackend(AbstractSourceBackend):
    source_type = "resana"

    def __init__(self):
        self._user = None

    def _get_client(self) -> InterstisClient:
        """Return an authenticated InterstisClient for the current user context.

        Raises RuntimeError if no user has been set yet (programming error).
        """
        if self._user is None:
            raise RuntimeError(
                "No user context set on ResanaSourceBackend. "
                "Call get_workspaces() or get_workspace_structure() first."
            )
        token = ResanaTokenManager(self._user).get_valid_token()
        return InterstisClient(token)

    def get_workspaces(self, user) -> list[SourceWorkspace]:
        self._user = user
        client = self._get_client()
        return [
            SourceWorkspace(id=ws["uuid"], title=ws["name"], raw_data=ws)
            for ws in client.get_workspaces()
        ]

    def get_workspace_structure(self, workspace) -> SourceFolder:
        self._user = workspace.migration_user
        client = self._get_client()
        return self._explore_folder(workspace.source_id, "", client)

    def download_file(self, file: SourceFile, destination_path: str) -> None:
        client = self._get_client()
        client.download_file(file.download_url, destination_path)

    def _explore_folder(self, uuid: str, name: str, client) -> SourceFolder:
        """Recursively fetch a folder's contents via the Interstis explore endpoint.

        The API returns one level at a time, so each child folder requires a
        separate explore() call.
        """
        members = client.explore(uuid)
        folder = SourceFolder(name=name)
        if not members:
            return folder
        raw = members[0]
        for raw_child in raw.get("folders", []):
            folder.children.append(
                self._explore_folder(
                    raw_child["uuid"], raw_child.get("name", ""), client
                )
            )
        for raw_file in raw.get("files", []):
            extension = raw_file.get("extension", "")
            if extension and not extension.startswith("."):
                extension = "." + extension
            folder.files.append(
                SourceFile(
                    id=raw_file["uuid"],
                    name=raw_file.get("name", ""),
                    extension=extension,
                    download_url=raw_file["uuid"],
                    raw_data=raw_file,
                )
            )
        return folder
