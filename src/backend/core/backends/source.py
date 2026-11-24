"""Abstract source backend interface, source data types, and source manager."""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.module_loading import import_string

from core.backends.organization_sync import release_organization_sync
from core.models import Organization, Workspace

logger = logging.getLogger(__name__)


class SourceTokenExpired(Exception):
    """The user's token for the source can no longer be used or refreshed:
    they must log in again."""


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
class SourceOrganization:
    """An organization the user administers on the source platform."""

    id: str
    name: str


@dataclass
class SourceWorkspace:
    """A workspace available for migration from a source backend."""

    id: str
    title: str
    raw_data: dict = field(default_factory=dict)
    # Set when the source names the organization. Personal spaces leave it empty.
    organization_id: str | None = None
    organization_name: str = ""
    # Portal numeric id when the source has a second namespace (Resana slug).
    portal_id: str | None = None


def truncate_folder_files(folder: SourceFolder, limit: int) -> bool:
    """
    Keep only the first `limit` files in the tree, in pre-order (a folder's own
    files first, then its children in list order). Mutates `folder` in place.

    Returns True if any file was removed, i.e. the tree had more than `limit`
    files.
    """
    remaining = limit
    truncated = False

    def visit(node: SourceFolder) -> None:
        nonlocal remaining, truncated
        if len(node.files) > remaining:
            truncated = True
        node.files = node.files[:remaining]
        remaining -= len(node.files)
        for child in node.children:
            visit(child)

    visit(folder)
    return truncated


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
    label: str  # Human-readable label, e.g. "Osmose" — used in migration mail content
    portal_organization_ids: bool = False

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Skip check for abstract intermediate classes
        if getattr(cls, "__abstractmethods__", None):
            return

        if missing := [
            attr for attr in ("source_type", "label") if not hasattr(cls, attr)
        ]:
            raise TypeError(
                f"{cls.__name__} must define class attribute(s): {', '.join(missing)}"
            )

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
        destinations are invoked.

        Implementations should populate workspace.members with the list of workspace
        members fetched from the source platform, then call workspace.save().
        Each entry must be a dict with keys: name, firstName, email.

        Destination backends consume workspace.members to share/invite users or
        generate platform-specific member files (e.g. osmose_users.csv for Resana,
        users_list_by_migrator.csv for archive).

        Default implementation is a no-op.
        """

    def begin_export(self, workspace) -> None:  # noqa: B027
        """
        Optional hook called before get_workspace_structure(), i.e. before any
        file is read or downloaded from the source.

        Implementations may use this to freeze the source workspace against
        concurrent edits and/or grant the migration account access it would
        otherwise lack, so the migration doesn't silently miss files (#215).

        Default implementation is a no-op.
        """

    def finalize_export(self, workspace) -> None:  # noqa: B027
        """
        Optional hook always called after begin_export(), in a `finally` block,
        even if get_workspace_structure(), the download, or a destination raises.

        Implementations should reverse whatever begin_export() did (release any
        access it granted, unfreeze the workspace) and must not raise: an
        exception here would replace/mask the migration's own exception, since
        this hook runs inside a `finally`. Swallow and log errors instead.

        Default implementation is a no-op.
        """

    def get_administered_organizations(self, _user) -> list[SourceOrganization]:
        """Organizations this user administers on the source platform."""

        return []

    def get_organization_workspaces(
        self, _user, _organization_id: str
    ) -> list[SourceWorkspace]:
        """Shared workspaces of one organization on the source platform."""

        return []

    def upsert_organization_workspace(
        self, organization, source_ws: SourceWorkspace
    ) -> Workspace | None:
        """Create or update one workspace row for an organization sync."""

        if not source_ws.id:
            return None
        workspace = Workspace.objects.filter(source_id=source_ws.id).first()
        if workspace is None:
            workspace = Workspace(source_id=source_ws.id, source_type=self.source_type)
        workspace.title = source_ws.title
        workspace.organization = organization
        workspace.save()
        return workspace


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
            # Absent means the payload did not name an organization. Leave a
            # previous link in place: the GED list often omits the field.
            if source_ws.organization_id:
                workspace.organization = _organization_for(source_ws)
            if source_ws.portal_id:
                workspace.portal_id = source_ws.portal_id
            workspace.save()
            user.workspaces.add(workspace)
        # Note: workspaces that disappear from the source are NOT deleted from the DB.
        # This is intentional — this tool is designed for one-shot migrations, not
        # continuous sync. Orphaned workspace records are harmless and preserve history.

    def sync_administered_organizations(self, user) -> None:
        """Upsert administered organizations and drop stale admin links for `user`."""
        backend = self.get_backend()
        entries = backend.get_administered_organizations(user)
        kept = []
        with transaction.atomic():
            for entry in entries:
                organization = Organization.from_external_id(
                    entry.id,
                    entry.name,
                    from_portal=backend.portal_organization_ids,
                )
                organization.admins.add(user)
                kept.append(organization.pk)
            stale = list(user.administered_organizations.exclude(pk__in=kept))
            for organization in stale:
                organization.admins.remove(user)
        logger.info(
            "Synced %s administered organizations for user %s",
            len(kept),
            user.pk,
        )

    def sync_organization_workspaces(self, user, organization_id: str) -> None:
        """Upsert shared workspaces of one organization. Drops the sync lock when done."""
        try:
            backend = self.get_backend()
            entries = backend.get_organization_workspaces(user, organization_id)
            now = timezone.now()
            synced = 0
            with transaction.atomic():
                organization = Organization.from_external_id(
                    organization_id,
                    "",
                    from_portal=backend.portal_organization_ids,
                )
                organization.admins.add(user)
                for source_ws in entries:
                    if (
                        backend.upsert_organization_workspace(organization, source_ws)
                        is not None
                    ):
                        synced += 1
                organization.last_sync = now
                organization.save()
            logger.info(
                "Synced %s workspaces of organization %s for user %s",
                synced,
                organization_id,
                user.pk,
            )
        finally:
            release_organization_sync(organization_id)


def _organization_for(source_ws: SourceWorkspace) -> Organization:
    """Return the organization of a source workspace, creating it when missing.

    Reuses a portal row when its id equals the GED id. A different GED id
    creates its own row and leaves portal_id empty.
    """
    return Organization.from_external_id(
        source_ws.organization_id,
        source_ws.organization_name,
        from_portal=False,
    )
