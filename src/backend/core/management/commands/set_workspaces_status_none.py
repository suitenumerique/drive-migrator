"""Command to set all workspaces status to NONE"""
from django.core.management.base import BaseCommand

from core.models import Workspace


class Command(BaseCommand):
    """Command to set all workspaces status to NONE"""

    help = "Set all workspaces status to NONE"

    def handle(self, *args, **options):
        workspaces = Workspace.objects.all()
        for workspace in workspaces:
            workspace.destination_statuses = {}
            workspace.destination_metadata = {}
            workspace.sync_status()
            workspace.save()
