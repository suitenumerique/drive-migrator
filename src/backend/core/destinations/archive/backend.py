"""ArchiveDestinationBackend — wraps ArchiveManager and implements AbstractDestinationBackend."""

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
        manager = ArchiveManager()
        manager.zip_workspace_folder(workspace)
        archive_url = manager.upload_archive(workspace)
        MailsManager().send_archive_download_mail(user, workspace, archive_url)
        workspace.set_destination_status("archive", Workspace.Status.SUCCESS)
        workspace.save()

    def get_download_url(self, workspace) -> str:
        manager = ArchiveManager()
        return manager.get_download_url(workspace)
