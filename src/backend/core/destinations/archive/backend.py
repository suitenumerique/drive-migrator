"""ArchiveDestinationBackend — wraps ArchiveManager and implements AbstractDestinationBackend."""

import csv
import os

from django.utils.translation import gettext_lazy as _

from core.backends.destination import AbstractDestinationBackend
from core.mails_manager import MailsManager
from core.models import Workspace
from core.processing.folder_helper import ArchiveManager


class ArchiveDestinationBackend(AbstractDestinationBackend):
    """
    Destination backend that zips the local workspace folder and uploads it to S3.

    Wraps ArchiveManager (existing logic) and exposes the AbstractDestinationBackend
    interface. The archive includes any files placed in the local folder by the source
    backend's prepare_export() hook (e.g. a members CSV for Osmose).
    """

    name = "archive"
    label = "Archive ZIP"

    def export(self, workspace, user, local_folder_path: str) -> None:
        csv_path = None
        if workspace.members:
            csv_path = os.path.join(local_folder_path, "users.csv")
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerows(
                    [m["name"], m["firstName"], m["email"]] for m in workspace.members
                )
        manager = ArchiveManager()
        manager.zip_workspace_folder(workspace)
        if csv_path:
            os.remove(csv_path)
        archive_url = manager.upload_archive(workspace)
        title = _(f"Votre archive de l'espace {workspace.title} est prête !")
        MailsManager().send_migration_mail(
            user,
            workspace,
            "archive_download",
            {"title": title, "download_url": archive_url},
        )
        workspace.set_destination_status("archive", Workspace.Status.SUCCESS)
        workspace.save()

    def get_download_url(self, workspace) -> str:
        manager = ArchiveManager()
        return manager.get_download_url(workspace)
