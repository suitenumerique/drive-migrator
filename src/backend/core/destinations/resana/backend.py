"""ResanaDestinationBackend — wraps ResanaBackend and implements AbstractDestinationBackend."""

import csv
import os

from core.backends.destination import AbstractDestinationBackend
from core.destinations.resana.resana_backend import ResanaBackend
from core.models import Workspace


class ResanaDestinationBackend(AbstractDestinationBackend):
    """
    Destination backend that uploads the workspace to Resana via an async import job.

    The export is asynchronous: create_workspace() submits the job and returns
    immediately. The destination status is set to PENDING; it will be updated to
    SUCCESS or FAILURE later by the refresh_job management command.
    """

    name = "resana"
    label = "Resana"

    def export(self, workspace, user, local_folder_path: str) -> None:
        if workspace.members:
            csv_path = os.path.join(local_folder_path, "osmose_users.csv")
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerows(
                    [m["name"], m["firstName"], m["email"]] for m in workspace.members
                )
        backend = ResanaBackend()
        backend.create_workspace(workspace, user)
        workspace.set_destination_status("resana", Workspace.Status.PENDING)
        workspace.save()

    def get_error_details(self, workspace) -> list:
        backend = ResanaBackend()
        return backend.get_error_details(workspace)

    def retry(self, workspace) -> None:
        backend = ResanaBackend()
        backend.retry_job(workspace)

    def poll_completion(self, workspace) -> None:
        backend = ResanaBackend()
        backend.refresh_job(workspace)
