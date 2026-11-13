"""Command to get the stalled migration runs moving again, or fail them."""

from django.core.management.base import BaseCommand

from core.processing.watchdog import watch_runs


class Command(BaseCommand):
    """Run by a cron job, see docs/migration-pipeline.md."""

    help = "Queue again the unfinished files of stalled migration runs, or fail them"

    def handle(self, *args, **options):
        watch_runs()
