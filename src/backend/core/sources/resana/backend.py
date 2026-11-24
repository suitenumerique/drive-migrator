"""ResanaSourceBackend — reads workspaces from the Interstis GED API."""

import html
import logging

from django.conf import settings
from django.db import DatabaseError

import requests
from cryptography.fernet import InvalidToken

from core.backends.source import (
    AbstractSourceBackend,
    SourceFile,
    SourceFolder,
    SourceOrganization,
    SourceWorkspace,
)
from core.models import Workspace
from core.sources.resana.interstis_client import InterstisClient
from core.sources.resana.resana_lock_client import ResanaLockClient, ResanaLockError
from core.sources.resana.resana_members_client import (
    ANIMATEUR_LABEL,
    GESTIONNAIRE_CODE,
    ROLE_LABELS,
    ResanaMembersClient,
)
from core.sources.resana.token_manager import ResanaTokenExpired, ResanaTokenManager

logger = logging.getLogger(__name__)


def _tab_ids(droit: dict) -> list[str]:
    tab = droit.get("tabId")
    if tab is None:
        tab = droit.get("selected") or []
    if not isinstance(tab, list):
        tab = [tab]
    return [str(item) for item in tab]


def _folder_depth(folder: dict, by_id: dict) -> int:
    depth = 0
    seen = set()
    parent_id = folder.get("dossier_mere")
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        depth += 1
        parent = by_id.get(parent_id)
        if parent is None:
            break
        parent_id = parent.get("dossier_mere")
    return depth


def _permission_blocks_migration(folder: dict, user_id: str) -> bool:
    """True when the folder's own droit would hide files from the migration account."""
    droit = folder.get("droit")
    if not isinstance(droit, dict):
        return False
    droit_type = droit.get("type") or ""
    if droit_type not in ("groupe", "utilisateur"):
        return False
    return not (droit_type == "utilisateur" and _tab_ids(droit) == [str(user_id)])


def _blocking_folders(folders: list[dict], user_id: str) -> list[dict]:
    """Folders whose droit we drop, deepest first so a parent is not blocked by a child."""
    by_id = {folder["id"]: folder for folder in folders}
    blocking = [
        folder for folder in folders if _permission_blocks_migration(folder, user_id)
    ]
    blocking.sort(key=lambda folder: _folder_depth(folder, by_id), reverse=True)
    return blocking


# finalize_export() runs in a `finally` in the export task: it must never raise, or it
# would replace the migration's own exception. These are the exceptions its PHP-portal
# calls can realistically raise; anything else is a genuine bug and should surface.
_FINALIZE_EXPORT_EXCEPTIONS = (
    requests.RequestException,
    ResanaLockError,
    ResanaTokenExpired,
    InvalidToken,
    ValueError,
    KeyError,
)


PORTAL_SOURCE_PREFIX = "portal:"


_ADMIN_ORG_ROLE_LABELS = {"administrateur"}
_ADMIN_ORG_ROLE_CODES = {"ADMINISTRATEUR"}


def _organization_admin_role(entry: dict) -> bool | None:
    """True when the entry is an organisation Administrateur.

    Interstis getPerimetre also returns Ambassadeur and Animateur
    d'organisation. The documented sample has no role key; None means we
    cannot tell and the caller must keep the row.
    """
    label = str(entry.get("profilDroitLibelle") or entry.get("role") or "").strip()
    code = str(entry.get("profilDroitCode") or entry.get("droit") or "").strip()
    if not label and not code:
        return None
    return (
        label.casefold() in _ADMIN_ORG_ROLE_LABELS
        or code.upper() in _ADMIN_ORG_ROLE_CODES
    )


class ResanaSourceBackend(AbstractSourceBackend):
    source_type = "resana"
    label = "Resana"
    portal_organization_ids = True

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

    def _get_members_client(
        self, access_token: str | None = None
    ) -> ResanaMembersClient:
        manager = ResanaTokenManager(self._user)
        return ResanaMembersClient(
            access_token=(
                manager.get_valid_token() if access_token is None else access_token
            ),
            session_id=manager.get_session_id(),
            csrf_token=manager.get_csrf_token(),
            base_url=settings.RESANA_WEB_ENDPOINT,
        )

    def _get_lock_client(self, access_token: str | None = None) -> ResanaLockClient:
        manager = ResanaTokenManager(self._user)
        return ResanaLockClient(
            access_token=(
                manager.get_valid_token() if access_token is None else access_token
            ),
            session_id=manager.get_session_id(),
            csrf_token=manager.get_csrf_token(),
            base_url=settings.RESANA_WEB_ENDPOINT,
        )

    def get_workspaces(self, user) -> list[SourceWorkspace]:
        """Return workspaces where `user` can migrate: shared workspaces where the
        user holds the GESTIONNAIRE (Animateur) role, and personal workspaces
        only when RESANA_MIGRATE_PERSONAL_WORKSPACES is enabled (issue #163).

        Resana's GED API lists every workspace the user belongs to regardless of
        role, so Lecteur/Contributeur-only workspaces are filtered out here using
        the PHP portal's role data (not exposed by the GED API).
        """
        self._user = user
        client = self._get_client()
        members_client = self._get_members_client()
        # The role listing has no GED uuid: join it through the PHP slug, since
        # matching on the name would also list a homonym the user doesn't manage.
        manager_slugs = {
            ws["slug"]
            for ws in members_client.get_workspaces_with_role()
            if ws["role_code"] == GESTIONNAIRE_CODE
        }
        manager_uuids = {
            ws["uuid"]
            for ws in members_client.get_workspaces()
            if ws["slug"] in manager_slugs
        }
        raw_workspaces = client.get_workspaces()
        unresolved_uuids = {
            ws["uuid"]
            for ws in raw_workspaces
            if not ws.get("isPersonalWorkspace") and ws["uuid"] not in manager_uuids
        }
        manager_uuids |= self._get_managed_locked_workspace_uuids(
            members_client, unresolved_uuids
        )

        workspaces = []
        for ws in raw_workspaces:
            name = html.unescape(ws["name"])
            if ws.get("isPersonalWorkspace"):
                if not settings.RESANA_MIGRATE_PERSONAL_WORKSPACES:
                    continue
            elif ws["uuid"] not in manager_uuids:
                continue
            organization_id, organization_name = _shared_organization(ws)
            workspaces.append(
                SourceWorkspace(
                    id=ws["uuid"],
                    title=name,
                    raw_data=ws,
                    organization_id=organization_id,
                    organization_name=organization_name,
                )
            )
        return workspaces

    def get_administered_organizations(self, user) -> list[SourceOrganization]:
        self._user = user
        ResanaTokenManager(user).ensure_offline_token()
        entries = self._get_members_client().get_administered_organizations()
        can_filter = any(
            _organization_admin_role(entry) is not None for entry in entries
        )
        if entries and not can_filter:
            logger.warning(
                "getPerimetre entries have no role field; keeping all organisations"
            )
        organizations = []
        for entry in entries:
            if can_filter and not _organization_admin_role(entry):
                continue
            source_id = _portal_entry_id(entry)
            if source_id is None:
                continue
            organizations.append(
                SourceOrganization(
                    id=source_id,
                    name=_portal_organization_name(entry, ""),
                )
            )
        return organizations

    def get_organization_workspaces(
        self, user, organization_id: str
    ) -> list[SourceWorkspace]:
        self._user = user
        ResanaTokenManager(user).ensure_offline_token()
        entries = self._get_members_client().get_organization_workspaces(
            organization_id
        )
        workspaces = []
        for entry in entries:
            portal_id = _portal_entry_id(entry)
            if portal_id is None:
                continue
            title = html.unescape(str(entry.get("nom") or portal_id))
            workspaces.append(
                SourceWorkspace(
                    id=f"{PORTAL_SOURCE_PREFIX}{portal_id}",
                    title=title,
                    organization_id=organization_id,
                    raw_data={"portal_id": portal_id},
                )
            )
        return workspaces

    def upsert_organization_workspace(
        self, organization, source_ws: SourceWorkspace
    ) -> Workspace | None:
        portal_id = source_ws.raw_data.get("portal_id")
        if not portal_id and source_ws.id.startswith(PORTAL_SOURCE_PREFIX):
            portal_id = source_ws.id.removeprefix(PORTAL_SOURCE_PREFIX)
        if not portal_id:
            logger.warning("Skipping a portal workspace with no id")
            return None
        placeholder = f"{PORTAL_SOURCE_PREFIX}{portal_id}"
        workspace = Workspace.objects.filter(portal_id=portal_id).first()
        if workspace is None:
            workspace = Workspace.objects.filter(source_id=placeholder).first()
        if workspace is None:
            workspace = Workspace(
                portal_id=portal_id,
                source_id=placeholder,
                source_type=self.source_type,
                title=source_ws.title,
                organization=organization,
            )
            workspace.save()
            return workspace
        workspace.portal_id = portal_id
        workspace.title = source_ws.title
        workspace.organization = organization
        if not workspace.source_type:
            workspace.source_type = self.source_type
        if not workspace.source_id:
            workspace.source_id = placeholder
        workspace.save()
        return workspace

    @staticmethod
    def _get_managed_locked_workspace_uuids(
        members_client, candidate_uuids: set[str]
    ) -> set[str]:
        """Return the uuids among `candidate_uuids` of locked workspaces the user
        is Animateur of.

        Locked workspaces are missing from listerMesEspacesV2, so the role is read
        from each workspace page instead. Any doubt excludes the workspace.
        """
        if not candidate_uuids:
            return set()
        try:
            locked_workspaces = members_client.get_locked_workspaces()
        # The listing is parsed without validation: an unexpected payload shape
        # surfaces as KeyError/TypeError/AttributeError, and must not fail the sync.
        except (
            requests.RequestException,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
        ):
            logger.warning(
                "Could not list locked Resana workspaces, skipping them",
                exc_info=True,
            )
            return set()
        uuids = set()
        for ws in locked_workspaces:
            if ws["uuid"] not in candidate_uuids:
                continue
            try:
                role_label = members_client.get_own_role_label(ws["slug"])
            except requests.RequestException:
                logger.warning(
                    "Could not read role on locked Resana workspace %s, skipping it",
                    ws["slug"],
                    exc_info=True,
                )
                continue
            if role_label is None:
                logger.warning(
                    "No role badge on locked Resana workspace %s, skipping it",
                    ws["slug"],
                )
            elif role_label == ANIMATEUR_LABEL:
                uuids.add(ws["uuid"])
            elif role_label not in ROLE_LABELS:
                logger.warning(
                    "Unexpected role %r on locked Resana workspace %s, skipping it",
                    role_label,
                    ws["slug"],
                )
        return uuids

    def get_workspace_structure(self, workspace) -> SourceFolder:
        self._user = workspace.migration_user
        client = self._get_client()
        return self._explore_folder(workspace.source_id, "", client)

    def download_file(self, file: SourceFile, destination_path: str) -> None:
        client = self._get_client()
        client.download_file(file.download_url, destination_path)

    def prepare_export(self, workspace, local_folder_path: str) -> None:
        self._user = workspace.migration_user
        client = self._get_members_client()
        slug = client.find_slug_by_workspace_uuid(workspace.source_id)
        if slug is None:
            logger.warning(
                "No Resana slug found for workspace %s (GED uuid %s), skipping members",
                workspace.id,
                workspace.source_id,
            )
            return
        workspace.members = client.list_workspace_members(slug)
        workspace.save()

    def begin_export(self, workspace) -> None:
        """Freeze the workspace and grant our migration account access to every folder.

        Runs before any file is read, so a concurrent edit can't be missed and a
        folder we otherwise lack rights on doesn't get silently skipped (#215).
        Lists folders once: drops blocking droits first, then grants root access.

        Idempotent: if the workspace is already locked, or a folder is already
        restricted to exactly our account, we don't touch it again, and we
        record exactly what *we* changed this run, so finalize_export() only
        reverses that.
        """
        self._user = workspace.migration_user
        # A task that waited in the queue past the offline token idle timeout
        # must fail here, not hours later with the workspace left locked.
        ResanaTokenManager(self._user).ensure_offline_token()
        members_client = self._get_members_client()
        slug = members_client.find_slug_by_workspace_uuid(workspace.source_id)
        if slug is None:
            logger.warning(
                "No Resana slug found for workspace %s (GED uuid %s), "
                "migrating without locking it",
                workspace.id,
                workspace.source_id,
            )
            return

        lock_client = self._get_lock_client()
        previous_state = workspace.source_lock_state or {}

        already_locked = members_client.is_workspace_locked(slug)

        # Merge with a previous run's state: if it was killed before
        # finalize_export(), what it locked/granted is still ours to reverse.
        # Each change is recorded before its remote call, so a crash in between
        # can't leave a change we don't know to reverse (reversing a change that
        # didn't happen is harmless).
        granted_folder_ids = list(previous_state.get("folders_granted_by_us", []))
        workspace.source_lock_state = {
            **previous_state,
            "slug": slug,
            "workspace_locked_by_us": (
                previous_state.get("workspace_locked_by_us", False)
                or not already_locked
            ),
            "folders_granted_by_us": granted_folder_ids,
        }
        workspace.save(update_fields=["source_lock_state"])

        if not already_locked:
            lock_client.lock_workspace(slug)
            if not members_client.is_workspace_locked(slug):
                raise ResanaLockError(f"Workspace {slug} is still unlocked after figer")

        user_id = lock_client.get_connected_user_id()
        folders = lock_client.list_folders(slug)
        self._drop_blocking_folder_permissions(
            workspace, lock_client, slug, user_id, folders
        )
        root_ids = [
            folder["id"] for folder in folders if folder.get("dossier_mere") is None
        ]
        for folder_id in root_ids:
            if folder_id in granted_folder_ids:
                continue
            current_owners = lock_client.get_folder_access_owners(slug, folder_id)
            if current_owners == [user_id]:
                continue
            granted_folder_ids.append(folder_id)
            workspace.save(update_fields=["source_lock_state"])
            lock_client.grant_folder_access(slug, folder_id, user_id)

    def finalize_export(self, workspace) -> None:
        """Reverse begin_export(): release only the folders we granted, then unlock
        only if we locked the workspace ourselves (#215).

        Always runs, even if the migration failed. Never raises: every call is
        best-effort and logged individually so one failure doesn't prevent the
        rest of the cleanup, and so it can't mask the migration's own exception
        (this runs inside a `finally`).
        """
        lock_state = dict(workspace.source_lock_state or {})
        slug = lock_state.get("slug")
        if slug is None:
            return

        self._user = workspace.migration_user
        try:
            # Fetched once: in the last minute of the access token, a second
            # Keycloak timeout would outlast it.
            token = ResanaTokenManager(self._user).get_token_for_cleanup()
            lock_client = self._get_lock_client(access_token=token)
            members_client = self._get_members_client(access_token=token)
        except _FINALIZE_EXPORT_EXCEPTIONS:
            logger.error(
                "Could not build clients to finalize export for workspace %s, "
                "Resana lock state left to reverse by hand: %s",
                workspace.id,
                lock_state,
                exc_info=True,
            )
            return

        # Only what failed to be reversed stays recorded, for a later retry.
        remaining_folder_ids = []
        for folder_id in lock_state.get("folders_granted_by_us", []):
            try:
                lock_client.release_folder_access(slug, folder_id)
            except _FINALIZE_EXPORT_EXCEPTIONS:
                remaining_folder_ids.append(folder_id)
                logger.error(
                    "Could not release access on folder %s of Resana workspace %s "
                    "(workspace %s)",
                    folder_id,
                    slug,
                    workspace.id,
                    exc_info=True,
                )
        lock_state["folders_granted_by_us"] = remaining_folder_ids

        if lock_state.get("workspace_locked_by_us", False):
            try:
                self._unlock_workspace(lock_client, members_client, slug)
                lock_state["workspace_locked_by_us"] = False
            except _FINALIZE_EXPORT_EXCEPTIONS:
                logger.error(
                    "Could not unlock Resana workspace %s (workspace %s)",
                    slug,
                    workspace.id,
                    exc_info=True,
                )
        else:
            logger.info(
                "Workspace %s was not locked by us (or lock state is unknown), "
                "leaving its lock state untouched.",
                workspace.id,
            )

        # Root deverouille can cascade and put a child droit back.
        self._drop_recorded_permissions(workspace, lock_client, slug, lock_state)

        workspace.source_lock_state = lock_state
        try:
            workspace.save(update_fields=["source_lock_state"])
        except DatabaseError:
            logger.error(
                "Could not save source_lock_state for workspace %s, "
                "Resana lock state left to reverse: %s",
                workspace.id,
                lock_state,
                exc_info=True,
            )

    @staticmethod
    def _drop_blocking_folder_permissions(
        workspace, lock_client, slug, user_id, folders
    ) -> None:
        """Delete explicit folder droits that hide files, deepest folder first.

        `folders` is the list already fetched by the caller (one getFolders).
        Ids are collected before the deletions, then persisted afterwards. They
        are not granted droits: finalize_export drops them again instead of
        restoring them.
        """
        blocking = _blocking_folders(folders, user_id)
        if not blocking:
            return
        removed = list(workspace.source_lock_state.get("permissions_removed") or [])
        known = {item["id"] for item in removed}
        for folder in blocking:
            if folder["id"] not in known:
                removed.append({"id": folder["id"], "name": folder.get("name", "")})
            lock_client.release_folder_access(slug, folder["id"])
        workspace.source_lock_state["permissions_removed"] = removed
        workspace.save(update_fields=["source_lock_state"])

    @staticmethod
    def _drop_recorded_permissions(workspace, lock_client, slug, lock_state) -> None:
        """Delete again the folders already recorded. The record itself stays."""
        for folder in lock_state.get("permissions_removed") or []:
            try:
                lock_client.release_folder_access(slug, folder["id"])
            except _FINALIZE_EXPORT_EXCEPTIONS:
                logger.error(
                    "Could not drop permission on folder %s of Resana workspace %s "
                    "(workspace %s)",
                    folder.get("id"),
                    slug,
                    workspace.id,
                    exc_info=True,
                )

    @staticmethod
    def _unlock_workspace(lock_client, members_client, slug: str) -> None:
        """Unlock the workspace, raising ResanaLockError if it still reads as locked."""
        lock_client.unlock_workspace(slug)
        if members_client.is_workspace_locked(slug):
            raise ResanaLockError(f"Workspace {slug} is still locked after defiger")

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
                    raw_child["uuid"], html.unescape(raw_child.get("name", "")), client
                )
            )
        for raw_file in raw.get("files", []):
            extension = raw_file.get("extension", "")
            if extension and not extension.startswith("."):
                extension = "." + extension
            folder.files.append(
                SourceFile(
                    id=raw_file["uuid"],
                    name=html.unescape(raw_file.get("name", "")),
                    extension=extension,
                    download_url=raw_file["uuid"],
                    raw_data=raw_file,
                )
            )
        return folder


def _shared_organization(workspace: dict) -> tuple[str | None, str]:
    """Return the organization id and name carried by a GED workspace.

    Personal spaces are never attached. The field is optional and may be a
    string id or an object with id/uuid and name/nom.
    """
    if workspace.get("isPersonalWorkspace"):
        return None, ""
    raw = workspace.get("organization")
    if isinstance(raw, str) and raw:
        return raw, ""
    if isinstance(raw, dict):
        org_id = raw.get("id") or raw.get("uuid")
        if org_id:
            name = raw.get("name") or raw.get("nom") or ""
            return str(org_id), html.unescape(str(name)) if name else ""
    return None, ""


def _portal_entry_id(entry: dict) -> str | None:
    raw = entry.get("id")
    if raw in (None, ""):
        logger.warning("Skipping a portal entry with no id")
        return None
    return str(raw)


def _portal_organization_name(entry: dict, fallback: str) -> str:
    name = entry.get("name")
    if not name:
        return fallback
    return html.unescape(str(name))
