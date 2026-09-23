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
from core.sources.resana.resana_lock_client import ResanaLockClient, ResanaLockError
from core.sources.resana.resana_members_client import (
    GESTIONNAIRE_CODE,
    ResanaMembersClient,
)
from core.sources.resana.token_manager import ResanaTokenExpired, ResanaTokenManager

logger = logging.getLogger(__name__)

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

    def _get_members_client(self) -> ResanaMembersClient:
        manager = ResanaTokenManager(self._user)
        return ResanaMembersClient(
            access_token=manager.get_valid_token(),
            session_id=manager.get_session_id(),
            csrf_token=manager.get_csrf_token(),
            base_url=settings.RESANA_WEB_ENDPOINT,
        )

    def _get_lock_client(self) -> ResanaLockClient:
        manager = ResanaTokenManager(self._user)
        return ResanaLockClient(
            access_token=manager.get_valid_token(),
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
        manager_names = {
            ws["name"]
            for ws in members_client.get_workspaces_with_role()
            if ws["role_code"] == GESTIONNAIRE_CODE
        }

        workspaces = []
        for ws in client.get_workspaces():
            name = html.unescape(ws["name"])
            if ws.get("isPersonalWorkspace"):
                if not settings.RESANA_MIGRATE_PERSONAL_WORKSPACES:
                    continue
            elif name not in manager_names:
                continue
            workspaces.append(SourceWorkspace(id=ws["uuid"], title=name, raw_data=ws))
        return workspaces

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
        slug = client.find_slug_by_workspace_name(workspace.title)
        if slug is None:
            return
        workspace.members = client.list_workspace_members(slug)
        workspace.save()

    def begin_export(self, workspace) -> None:
        """Freeze the workspace and grant our migration account access to every folder.

        Runs before any file is read, so a concurrent edit can't be missed and a
        folder we otherwise lack rights on doesn't get silently skipped (#215).

        Idempotent: if the workspace is already locked, or a folder is already
        restricted to exactly our account, we don't touch it again, and we
        record exactly what *we* changed this run, so finalize_export() only
        reverses that.
        """
        self._user = workspace.migration_user
        members_client = self._get_members_client()
        slug = members_client.find_slug_by_workspace_name(workspace.title)
        if slug is None:
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
        for folder_id in lock_client.get_top_level_folder_ids(slug):
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
            lock_client = self._get_lock_client()
            members_client = self._get_members_client()
        except _FINALIZE_EXPORT_EXCEPTIONS:
            logger.warning(
                "Could not build clients to finalize export for workspace %s",
                workspace.id,
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
                logger.warning(
                    "Could not release access on folder %s for workspace %s",
                    folder_id,
                    workspace.id,
                    exc_info=True,
                )
        lock_state["folders_granted_by_us"] = remaining_folder_ids

        if lock_state.get("workspace_locked_by_us", False):
            try:
                self._unlock_workspace(lock_client, members_client, slug)
                lock_state["workspace_locked_by_us"] = False
            except _FINALIZE_EXPORT_EXCEPTIONS:
                logger.warning(
                    "Could not unlock workspace %s", workspace.id, exc_info=True
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
            logger.warning(
                "Could not save source_lock_state for workspace %s",
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
