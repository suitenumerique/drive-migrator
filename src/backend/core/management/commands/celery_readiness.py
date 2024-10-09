"""Command to refresh workspaces resana status via pooling jobs status"""
import sys
from pathlib import Path

from django.core.management.base import BaseCommand

READINESS_FILE = Path("/tmp/celery_ready")  # noqa: S108


class Command(BaseCommand):
    """Command to check the readiness of Celery"""

    help = "Check the readiness of Celery"

    def handle(self, *args, **options):
        if not READINESS_FILE.is_file():
            sys.exit(1)
        sys.exit(0)
