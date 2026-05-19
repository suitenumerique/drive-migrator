"""ResanaSourceBackend — reads workspaces from the Interstis GED API."""

from core.backends.source import (
    AbstractSourceBackend,
    SourceFile,
    SourceFolder,
    SourceWorkspace,
)
from core.sources.resana.interstis_client import InterstisClient


class ResanaSourceBackend(AbstractSourceBackend):
    source_type = "resana"

    def get_workspaces(self, user) -> list[SourceWorkspace]:
        client = InterstisClient()
        return [
            SourceWorkspace(id=ws["uuid"], title=ws["name"], raw_data=ws)
            for ws in client.get_workspaces()
        ]

    def get_workspace_structure(self, workspace) -> SourceFolder:
        client = InterstisClient()
        members = client.explore(workspace.source_id)
        if not members:
            return SourceFolder(name="")
        return self._convert_folder(members[0])

    def download_file(self, file: SourceFile, destination_path: str) -> None:
        client = InterstisClient()
        client.download_file(file.download_url, destination_path)

    def _convert_folder(self, raw: dict) -> SourceFolder:
        folder = SourceFolder(name=raw.get("name", ""))
        for raw_child in raw.get("folders", []):
            folder.children.append(self._convert_folder(raw_child))
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
