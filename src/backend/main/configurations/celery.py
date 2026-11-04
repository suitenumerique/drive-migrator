"""Celery task queue settings."""

from configurations import values


class CeleryMixin:
    """Redis broker, django-db result backend and JSON (de)serialization."""

    CELERY_BROKER_URL = values.Value(
        "redis://redis:6379/0",
        environ_name="REDIS_URL",
        environ_prefix=None,
    )
    # Redis hands an unacknowledged message to another worker after the
    # visibility timeout (1 hour by default): keep it above the longest task,
    # see docs/migration-pipeline.md.
    CELERY_BROKER_TRANSPORT_OPTIONS = values.DictValue({"visibility_timeout": 43200})
    CELERY_RESULT_BACKEND = "django-db"
    CELERY_CACHE_BACKEND = "django-cache"
    CELERY_TASK_SERIALIZER = "json"
    CELERY_RESULT_SERIALIZER = "json"
    CELERY_ACCEPT_CONTENT = ["json"]
    CELERY_RESULT_EXTENDED = True
    CELERY_TRACK_STARTED = True
