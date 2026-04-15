"""DriveDestinationBackend — uploads a workspace to La Suite Drive."""

import os

from core.backends.destination import AbstractDestinationBackend
from core.destinations.drive.drive_backend import DriveBackend
from core.models import Workspace


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
        token = backend.get_access_token()

        # Create the root workspace folder in Drive
        root = backend.create_folder(workspace.title, token=token)
        root_id = root["id"]
        workspace.set_destination_metadata("drive", {"workspace_id": root_id})

        # Recursively upload the local folder tree
        self._upload_tree(backend, token, local_folder_path, root_id)

        # Share with the migration user
        if workspace.migration_user and workspace.migration_user.email:
            drive_user = backend.find_user_by_email(
                workspace.migration_user.email, token=token
            )
            if drive_user:
                backend.share_with_user(root_id, drive_user["id"], token=token)
            else:
                backend.invite_by_email(
                    root_id, workspace.migration_user.email, token=token
                )

        workspace.set_destination_status("drive", Workspace.Status.SUCCESS)
        workspace.save()

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
