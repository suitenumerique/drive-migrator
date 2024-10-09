"""Command to refresh workspaces resana status via pooling jobs status"""
import sys
import time
from pathlib import Path

from django.core.management.base import BaseCommand

LIVENESS_FILE = Path("/tmp/celery_worker_heartbeat")  # noqa: S108


class Command(BaseCommand):
    """Command to check the liveness of Celery"""

    help = "Check the liveness of Celery"

    def handle(self, *args, **options):
        if not LIVENESS_FILE.is_file():
            print("Celery liveness file NOT found.")  # noqa: T201
            sys.exit(1)
        stats = LIVENESS_FILE.stat()
        heartbeat_timestamp = stats.st_mtime
        current_timestamp = time.time()
        time_diff = current_timestamp - heartbeat_timestamp
        if time_diff > 60:
            print(  # noqa: T201
                "Celery Worker liveness file timestamp DOES NOT matches the given constraint."
            )
            sys.exit(1)
        print(  # noqa: T201
            "Celery Worker liveness file found and timestamp matches the given constraint."
        )
        sys.exit(0)
