"""Tribu destination: sends the workspace archive to Tribu through upload-broker."""

from core.backends.destination import AbstractDestinationBackend
from core.destinations.tribu.upload_broker import UploadBrokerClient
from core.models import Workspace
from core.processing.folder_helper import ArchiveManager


def get_owner(workspace, user) -> dict:
    """Return the archive owner for Tribu.

    Tribu needs the first and last names. The user model does not store them,
    so we take them from the workspace members. If the user is not in the
    members, both names are the local part of the email address.
    """
    email = user.email
    member = next(
        (
            m
            for m in workspace.members or []
            if (m.get("email") or "").lower() == email.lower()
        ),
        {},
    )
    fallback = email.split("@")[0]
    return {
        "email": email,
        "first_name": member.get("firstName") or fallback,
        "last_name": member.get("name") or fallback,
    }


class TribuDestinationBackend(AbstractDestinationBackend):
    """Zip the workspace and send the archive to Tribu.

    upload-broker stores the archive and adds it to the Tribu migration queue.
    Tribu then imports it and sends the last mail to the user.
    """

    name = "tribu"
    label = "Tribu"

    def export(self, workspace, user, local_folder_path: str) -> None:
        if not user.email:
            raise ValueError(f"User {user.id} has no email. Tribu needs it.")

        manager = ArchiveManager()
        manager.zip_workspace_folder(workspace)

        try:
            UploadBrokerClient(user).upload(
                manager.get_archive_path(workspace),
                source_id=workspace.source_id,
                title=workspace.title,
                owner=get_owner(workspace, user),
            )
        finally:
            manager.delete_archive(workspace)

        workspace.set_destination_status(self.name, Workspace.Status.SUCCESS)
        workspace.save()
