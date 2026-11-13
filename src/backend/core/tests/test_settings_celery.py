"""Tests for the Celery settings the migration pipeline relies on."""

from django.conf import settings


def test_the_broker_redelivers_a_task_only_after_12_hours():
    """A worker running a long task keeps its next message reserved: Redis
    must not hand it to another worker before the task ends, or that export
    or file runs twice."""
    assert settings.CELERY_BROKER_TRANSPORT_OPTIONS["visibility_timeout"] == 43200
