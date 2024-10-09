"""Celery configuration file."""
import os
from pathlib import Path

from celery import Celery
from celery.signals import worker_ready, worker_shutdown
from configurations.importer import install

from main.bootstraps import LivenessProbe

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main.settings")
os.environ.setdefault("DJANGO_CONFIGURATION", "Development")

install(check_options=True)

app = Celery("main")

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

# Readiness + Liveness from https://medium.com/ambient-innovation/health-checks-for-celery-in-kubernetes-cf3274a3e106 )
READINESS_FILE = Path("/tmp/celery_ready")  # noqa: S108

# Add liveness probe for k8s
app.steps["worker"].add(LivenessProbe)


@worker_ready.connect
def worker_ready(**_):
    READINESS_FILE.touch()


@worker_shutdown.connect
def worker_shutdown(**_):
    READINESS_FILE.unlink(missing_ok=True)
