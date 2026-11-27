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
    SourceWorkspace,
)
from core.sources.resana.interstis_client import InterstisClient
from core.sources.resana.resana_lock_client import (
    ResanaChildDroitError,
    ResanaLockClient,
    ResanaLockError,
    ResanaParentDroitError,
)
from core.sources.resana.resana_members_client import (
    ANIMATEUR_LABEL,
    GESTIONNAIRE_CODE,
    ROLE_LABELS,
    ResanaMembersClient,
)
from core.sources.resana.token_manager import ResanaTokenExpired, ResanaTokenManager

logger = logging.getLogger(__name__)


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


def _folder_has_droit(folder: dict) -> bool:
    return _droit_ids(folder) is not None


def _droit_ids(folder: dict) -> list[str] | None:
    """User or group ids on a folder droit, or None when it has neither."""
    droit = folder.get("droit")
    if not isinstance(droit, dict):
        return None
    if (droit.get("type") or "") not in ("groupe", "utilisateur"):
        return None
    tab = droit.get("tabId")
    if tab is None:
        tab = droit.get("selected") or []
    if not isinstance(tab, list):
        tab = [tab]
    return [str(item) for item in tab]


def _limited_to_someone_else(folder: dict, owners, user_id: str) -> bool:
    """True when live owners or the folder payload droit are not exactly us."""
    ours = [str(user_id)]
    if owners is not None and [str(item) for item in owners] != ours:
        return True
    tab = _droit_ids(folder)
    return tab is not None and tab != ours


def _is_descendant(folder: dict, ancestor_id: str, by_id: dict) -> bool:
    parent_id = folder.get("dossier_mere")
    seen = set()
    while parent_id and parent_id not in seen:
        if parent_id == ancestor_id:
            return True
        seen.add(parent_id)
        parent = by_id.get(parent_id)
        if parent is None:
            return False
        parent_id = parent.get("dossier_mere")
    return False


def _descendants_with_droit(root_id: str, folders: list[dict]) -> list[dict]:
    """Descendants of `root_id` that have their own droit, shallowest first."""
    by_id = {folder["id"]: folder for folder in folders}
    descendants = [
        folder
        for folder in folders
        if folder["id"] != root_id
        and _folder_has_droit(folder)
        and _is_descendant(folder, root_id, by_id)
    ]
    descendants.sort(key=lambda folder: _folder_depth(folder, by_id))
    return descendants


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


class ResanaSourceBackend(AbstractSourceBackend):
    source_type = "resana"
    label = "Resana"

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
            workspaces.append(SourceWorkspace(id=ws["uuid"], title=name, raw_data=ws))
        return workspaces

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
        For each root, saveDroit first; recover from a child-droit or already-
        limited error by unlocking the blocker, then retry. The portal default
        inbox (defaut == "1") cannot be limited and is left as-is. Folder
        grants stay in place after the run; finalize_export() only unfreezes
        the workspace.

        Idempotent: if the workspace is already locked, or a folder is already
        restricted to exactly our account, we don't touch it again.
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
        # finalize_export(), the workspace lock is still ours to reverse.
        # The lock is recorded before its remote call, so a crash in between
        # can't leave a lock we don't know to reverse. A folder grant is
        # recorded only after saveDroit returns; a retry checks the live
        # owners and grants again when they are not us.
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
        for folder in folders:
            if folder.get("dossier_mere") is not None:
                continue
            if str(folder.get("defaut") or "") == "1":
                continue
            current_owners = lock_client.get_folder_access_owners(slug, folder["id"])
            if current_owners == [user_id]:
                continue
            self._grant_root_folder(
                workspace, lock_client, user_id, folder, folders, current_owners
            )

    def finalize_export(self, workspace) -> None:
        """Unlock the workspace if we locked it ourselves (#215). Folder
        saveDroit restrictions stay in place.

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
    def _grant_root_folder(  # noqa: PLR0913  # pylint: disable=too-many-arguments
        workspace, lock_client, user_id, root, folders, owners
    ) -> None:
        """saveDroit `root` to the starter, recovering from portal lock errors.

        A root whose live owners or payload droit are not exactly our user is
        unlocked first. Child droits: unlock descendants (shallowest first),
        then retry. Unlocking a child blocked by a locked parent unlocks that
        parent first. Any other saveDroit failure is raised without unlocking,
        so we never migrate a subset and never clear a droit we did not see.
        """
        slug = workspace.source_lock_state["slug"]
        by_id = {folder["id"]: folder for folder in folders}

        def grant() -> None:
            lock_client.grant_folder_access(slug, root["id"], user_id)
            granted = workspace.source_lock_state["folders_granted_by_us"]
            if root["id"] not in granted:
                granted.append(root["id"])
                workspace.save(update_fields=["source_lock_state"])

        def drop_descendants() -> None:
            for child in _descendants_with_droit(root["id"], folders):
                ResanaSourceBackend._release_folder(
                    workspace, lock_client, slug, child, by_id
                )

        if _limited_to_someone_else(root, owners, user_id):
            ResanaSourceBackend._release_folder(
                workspace, lock_client, slug, root, by_id
            )
        try:
            grant()
        except ResanaChildDroitError:
            drop_descendants()
            grant()

    @staticmethod
    def _release_folder(workspace, lock_client, slug, folder, by_id) -> None:
        """deverouille `folder`, unlocking a locked parent first if the portal asks."""
        try:
            lock_client.release_folder_access(slug, folder["id"])
        except ResanaParentDroitError:
            parent = by_id.get(folder.get("dossier_mere"))
            if parent is None:
                raise
            ResanaSourceBackend._release_folder(
                workspace, lock_client, slug, parent, by_id
            )
            lock_client.release_folder_access(slug, folder["id"])
        ResanaSourceBackend._record_permission_removed(workspace, folder)

    @staticmethod
    def _record_permission_removed(workspace, folder) -> None:
        removed = list(workspace.source_lock_state.get("permissions_removed") or [])
        if folder["id"] not in {item["id"] for item in removed}:
            removed.append({"id": folder["id"], "name": folder.get("name", "")})
            workspace.source_lock_state["permissions_removed"] = removed
            workspace.save(update_fields=["source_lock_state"])

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
