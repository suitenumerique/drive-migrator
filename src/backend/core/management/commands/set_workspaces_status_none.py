"""Command to set all workspaces status to NONE"""
from django.core.management.base import BaseCommand

from core.models import Workspace


class Command(BaseCommand):
    """Command to set all workspaces status to NONE"""

    help = "Set all workspaces status to NONE"

    def handle(self, *args, **options):
        workspaces = Workspace.objects.all()
        for workspace in workspaces:
            workspace.status = Workspace.Status.NONE
            workspace.status_resana = Workspace.Status.NONE
            workspace.status_archive = Workspace.Status.NONE
            workspace.resana_id = None
            workspace.resana_job_id = None
            workspace.save()
