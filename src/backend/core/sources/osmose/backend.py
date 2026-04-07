"""OsmoseSourceBackend — wraps OsmoseRealBackend and implements AbstractSourceBackend."""

import os

from django.conf import settings

from core.backends.source import (
    AbstractSourceBackend,
    SourceFile,
    SourceFolder,
    SourceWorkspace,
)
from core.sources.osmose.osmose_real_backend import OsmoseRealBackend


class OsmoseSourceBackend(AbstractSourceBackend):
    """
    Source backend for Osmose workspaces.

    Wraps OsmoseRealBackend (HTTP client) and exposes the AbstractSourceBackend
    interface by converting Osmose-specific types to generic source types.
    """

    source_type = "osmose"

    def get_workspaces(self, user) -> list[SourceWorkspace]:
        backend = OsmoseRealBackend()
        osmose_workspaces = backend.get_workspaces(user)
        return [
            SourceWorkspace(
                id=ws.id,
                title=ws.title,
                raw_data=ws.raw_data or {},
            )
            for ws in osmose_workspaces
        ]

    def get_workspace_structure(self, workspace) -> SourceFolder:
        backend = OsmoseRealBackend()
        osmose_folder = backend.get_workspace_documents_structure(workspace)
        return self._convert_folder(osmose_folder)

    def download_file(self, file: SourceFile, destination_path: str) -> None:
        backend = OsmoseRealBackend()
        backend.download_file(file.download_url, destination_path)

    def prepare_export(self, workspace, local_folder_path: str) -> None:
        backend = OsmoseRealBackend()
        backend.create_users_csv(workspace)

    def _convert_folder(self, osmose_folder) -> SourceFolder:
        """Recursively convert an OsmoseFolder tree to a SourceFolder tree."""
        source_folder = SourceFolder(name=osmose_folder.name)

        for child in osmose_folder.children:
            source_folder.children.append(self._convert_folder(child))

        for osmose_file in osmose_folder.files:
            raw_download_url = (
                osmose_file.raw_data.get("downloadUrl", "")
                if osmose_file.raw_data
                else ""
            )
            download_url = os.path.join(settings.OSMOSE_BASE_ENDPOINT, raw_download_url)
            source_folder.files.append(
                SourceFile(
                    id=osmose_file.raw_data.get("id", "")
                    if osmose_file.raw_data
                    else "",
                    name=osmose_file.name,
                    extension=osmose_file.extension,
                    download_url=download_url,
                    raw_data=osmose_file.raw_data or {},
                )
            )

        return source_folder
