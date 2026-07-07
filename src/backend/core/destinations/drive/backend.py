"""DriveDestinationBackend — uploads a workspace to La Suite Drive."""

import csv
import os

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from core.backends.destination import AbstractDestinationBackend
from core.destinations.drive.drive_backend import (
    DriveServiceAccountBackend,
    DriveUserTokenBackend,
)
from core.mails_manager import MailsManager
from core.models import Workspace


class DriveDestinationBackend(AbstractDestinationBackend):
    """
    Destination backend that creates a workspace in La Suite Drive.

    Two auth modes are supported via DRIVE_AUTH_MODE:
    - "service_account" (default): uses OAuth2 client_credentials grant and
      /external_api/v1.0/. The migration user is explicitly shared as owner.
    - "user_token": uses the authenticated user's ProConnect token and /api/v1.0/.
      Drive automatically assigns ownership to the token holder, so the migration
      user is excluded from the sharing step.
    """

    name = "drive"
    label = "La Suite Drive"

    def _make_backend(self, user):
        auth_mode = getattr(settings, "DRIVE_AUTH_MODE", "service_account")
        if auth_mode == "user_token":
            return DriveUserTokenBackend(user)
        return DriveServiceAccountBackend()

    def export(self, workspace, user, local_folder_path: str) -> None:
        backend = self._make_backend(user)

        root = backend.create_folder(workspace.title)
        root_id = root["id"]
        workspace.set_destination_metadata("drive", {"workspace_id": root_id})

        csv_path = self._write_users_csv(workspace, local_folder_path)
        try:
            self._upload_tree(backend, local_folder_path, root_id)
        finally:
            if csv_path:
                os.remove(csv_path)

        if getattr(settings, "DRIVE_SHARE_MEMBERS", True):
            self._share_members(backend, workspace, root_id)

        title = _("Votre espace {title} est prêt sur La Suite Drive !").format(title=workspace.title)
        MailsManager().send_migration_mail(
            user, workspace, "drive_ready", {"title": title}
        )

        workspace.set_destination_status("drive", Workspace.Status.SUCCESS)
        workspace.save()

    def _write_users_csv(self, workspace, local_folder_path: str) -> str | None:
        """Write the shared-users listing into the local folder, like the archive export."""
        if not workspace.members:
            return None
        csv_path = os.path.join(local_folder_path, "users.csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerows(
                [m.get("name", ""), m.get("firstName", ""), m.get("email", "")]
                for m in workspace.members
            )
        return csv_path

    def _share_members(self, backend, workspace, root_id: str) -> None:
        """Share root_id with all relevant emails, respecting the auth mode."""
        auth_mode = getattr(settings, "DRIVE_AUTH_MODE", "service_account")
        migration_email = (
            workspace.migration_user.email
            if workspace.migration_user and workspace.migration_user.email
            else None
        )

        # Collect all emails to share, deduplicating across migration_user + members.
        # In user_token mode the token holder is already owner — skip their email.
        emails_to_skip = set()
        if auth_mode == "user_token" and migration_email:
            emails_to_skip.add(migration_email)

        emails_to_share = set()
        if migration_email and migration_email not in emails_to_skip:
            emails_to_share.add(migration_email)
        for member in workspace.members or []:
            email = member.get("email", "")
            if email and email not in emails_to_skip:
                emails_to_share.add(email)

        for email in emails_to_share:
            self._share_with_email(backend, root_id, email)

    def _share_with_email(self, backend, item_id: str, email: str) -> None:
        drive_user = backend.find_user_by_email(email)
        if drive_user:
            backend.share_with_user(item_id, drive_user["id"])
        else:
            backend.invite_by_email(item_id, email)

    def _upload_tree(self, backend, local_path: str, drive_parent_id: str) -> None:
        """Recursively create Drive folders and upload files from the local tree."""
        for entry in sorted(os.scandir(local_path), key=lambda e: e.name):
            if entry.is_dir():
                folder = backend.create_subfolder(entry.name, parent_id=drive_parent_id)
                self._upload_tree(backend, entry.path, folder["id"])
            elif entry.is_file():
                item = backend.create_file_item(entry.name, parent_id=drive_parent_id)
                backend.upload_to_s3(item["policy"], entry.path)
                backend.notify_upload_ended(item["id"])
