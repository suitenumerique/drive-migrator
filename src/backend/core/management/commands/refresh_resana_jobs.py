"""Command to refresh workspaces resana status via pooling jobs status"""
from django.core.management.base import BaseCommand

from core.models import Workspace
from core.resana.resana_backend import ResanaBackend


class Command(BaseCommand):
    """Command to set all workspaces status to NONE"""

    help = "Refresh workspaces resana status via pooling jobs status"

    def handle(self, *args, **options):
        workspaces = Workspace.objects.filter(
            destination_statuses__resana=Workspace.Status.PENDING
        )
        self.stdout.write(f"Workspaces to process {len(workspaces)}")
        resana_backend = ResanaBackend()
        for workspace in workspaces:
            self.stdout.write(f"Processing {workspace.id} {workspace.title} ...")
            resana_backend.refresh_job(workspace)
