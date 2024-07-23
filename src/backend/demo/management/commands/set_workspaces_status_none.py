from django.core.management.base import BaseCommand, CommandError

from core.models import Workspace


class Command(BaseCommand):
    help = "Set all workspaces status to NONE"

    def handle(self, *args, **options):

        workspaces = Workspace.objects.all()
        for workspace in workspaces:
            workspace.status = Workspace.Status.NONE
            workspace.status_resana = Workspace.Status.NONE
            workspace.status_archive = Workspace.Status.NONE
            workspace.save()
