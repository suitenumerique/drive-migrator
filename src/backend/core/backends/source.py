"""Abstract source backend interface, source data types, and source manager."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from django.conf import settings
from django.utils.module_loading import import_string

from core.models import Workspace


@dataclass
class SourceFile:
    """A file available for download from a source backend."""

    id: str
    name: str  # Cleaned display name (no extension)
    extension: str  # With leading dot, e.g. ".pdf", or ""
    download_url: str  # Opaque URL or path — passed back to backend.download_file()
    raw_data: dict = field(default_factory=dict)

    @property
    def name_with_extension(self) -> str:
        return self.name + self.extension


@dataclass
class SourceFolder:
    """A folder node in a workspace's document tree."""

    name: str
    children: list["SourceFolder"] = field(default_factory=list)
    files: list[SourceFile] = field(default_factory=list)


@dataclass
class SourceWorkspace:
    """A workspace available for migration from a source backend."""

    id: str
    title: str
    raw_data: dict = field(default_factory=dict)


class AbstractSourceBackend(ABC):
    """
    Contract for any platform that can serve as a migration source.

    Implementations must be stateless and instantiable without arguments,
    as they are loaded via import_string(settings.SOURCE_BACKEND)().

    Each subclass must define `source_type` as a class attribute (e.g. "osmose",
    "filesystem"). This value is persisted in Workspace.source_type — it must never
    change once workspaces have been created from this backend.
    """

    source_type: str  # e.g. "osmose", "filesystem", "resana" — persisted in Workspace.source_type

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Skip check for abstract intermediate classes
        if not getattr(cls, "__abstractmethods__", None) and not hasattr(
            cls, "source_type"
        ):
            raise TypeError(f"{cls.__name__} must define class attribute 'source_type'")

    @abstractmethod
    def get_workspaces(self, user) -> list[SourceWorkspace]:
        """
        Return the list of workspaces accessible to the user on this source platform.
        Used by the synchronize endpoint to populate the DB.
        """

    @abstractmethod
    def get_workspace_structure(self, workspace) -> SourceFolder:
        """
        Return the full folder/file tree for a workspace.
        The root SourceFolder is virtual — its children are the top-level folders.
        """

    @abstractmethod
    def download_file(self, file: SourceFile, destination_path: str) -> None:
        """
        Download `file` to `destination_path` on the local filesystem.
        Must handle retries and transient errors internally.
        """

    def prepare_export(self, workspace, local_folder_path: str) -> None:  # noqa: B027
        """
        Optional hook called after the local folder is assembled and before
        destinations are invoked. Use this to write source-specific supplementary
        files into the export directory (e.g. a members CSV for Osmose).
        Default implementation is a no-op.
        """


class SourceManager:
    """
    Loads the source backend configured in settings.SOURCE_BACKEND and provides
    a synchronize() method to populate the DB with workspaces from that source.
    """

    def get_backend(self) -> AbstractSourceBackend:
        backend_class = import_string(settings.SOURCE_BACKEND)
        return backend_class()

    def synchronize(self, user) -> None:
        backend = self.get_backend()
        source_workspaces = backend.get_workspaces(user)
        source_type = backend.source_type

        for source_ws in source_workspaces:
            workspace = Workspace.objects.filter(source_id=source_ws.id).first()
            if not workspace:
                workspace = Workspace(source_id=source_ws.id, source_type=source_type)
            workspace.title = source_ws.title
            workspace.save()
            user.workspaces.add(workspace)
        # Note: workspaces that disappear from the source are NOT deleted from the DB.
        # This is intentional — this tool is designed for one-shot migrations, not
        # continuous sync. Orphaned workspace records are harmless and preserve history.
