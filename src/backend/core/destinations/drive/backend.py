"""DriveDestinationBackend — uploads a workspace to La Suite Drive."""

import logging
import os

import requests

from core.backends.destination import AbstractDestinationBackend
from core.destinations.drive.drive_backend import DriveBackend
from core.models import Workspace

logger = logging.getLogger(__name__)


class DriveDestinationBackend(AbstractDestinationBackend):
    """
    Destination backend that creates a workspace in La Suite Drive.

    The export is synchronous: folder structure is created, files are uploaded
    via the 3-step process (create item → PUT S3 → upload-ended), then members
    are shared or invited.
    """

    name = "drive"
    label = "La Suite Drive"

    def export(self, workspace, user, local_folder_path: str) -> None:
        backend = DriveBackend()
        service_token = backend.get_access_token()

        migration_email = (
            workspace.migration_user.email if workspace.migration_user else None
        )
        user_token = (
            self._resolve_user_token(backend, service_token, migration_email)
            if migration_email
            else None
        )
        token = user_token or service_token

        root = backend.create_folder(workspace.title, token=token)
        root_id = root["id"]
        workspace.set_destination_metadata("drive", {"workspace_id": root_id})

        self._upload_tree(backend, token, local_folder_path, root_id)

        if not user_token and migration_email:
            drive_user = backend.find_user_by_email(migration_email, token=token)
            if drive_user:
                backend.share_with_user(root_id, drive_user["id"], token=token)
            else:
                backend.invite_by_email(root_id, migration_email, token=token)

        for member in workspace.members or []:
            email = member.get("email", "")
            if not email:
                continue
            drive_user = backend.find_user_by_email(email, token=token)
            if drive_user:
                backend.share_with_user(root_id, drive_user["id"], token=token)
            else:
                backend.invite_by_email(root_id, email, token=token)

        workspace.set_destination_status("drive", Workspace.Status.SUCCESS)
        workspace.save()

    def _resolve_user_token(
        self, backend: DriveBackend, service_token: str, email: str
    ) -> str | None:
        """
        Try to get a token impersonating the migration user via Keycloak token exchange.

        Admin API errors propagate (broken config). Exchange errors → None (fallback).
        """
        sub = backend.find_user_sub_by_email(email, service_token)
        if not sub:
            logger.info("User %s not found in Keycloak Drive realm, using fallback", email)
            return None
        try:
            user_token = backend.exchange_token(service_token, sub)
            logger.info("Token exchange successful for %s", email)
            return user_token
        except requests.HTTPError as exc:
            logger.warning("Token exchange failed for %s: %s", email, exc)
            return None

    def _upload_tree(
        self, backend: DriveBackend, token: str, local_path: str, drive_parent_id: str
    ) -> None:
        """Recursively create Drive folders and upload files from the local tree."""
        for entry in sorted(os.scandir(local_path), key=lambda e: e.name):
            if entry.is_dir():
                folder = backend.create_subfolder(
                    entry.name, parent_id=drive_parent_id, token=token
                )
                self._upload_tree(backend, token, entry.path, folder["id"])
            elif entry.is_file():
                item = backend.create_file_item(
                    entry.name, parent_id=drive_parent_id, token=token
                )
                backend.upload_to_s3(item["policy"], entry.path)
                backend.notify_upload_ended(item["id"], token=token)
