"""Logging configuration shared by the web and Celery processes."""

from configurations import values


class LoggingMixin:
    """Console logging whose root and app levels are tunable through env vars."""

    # Logging: the web process logs to the console through the root handler.
    # Celery workers replace it with their own (no duplicated lines) and set
    # the root and task loggers to their -l level.
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "simple": {
                "format": "{asctime} {name} {levelname} {message}",
                "style": "{",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "simple",
            },
        },
        "root": {
            "handlers": ["console"],
            # Upper-cased: logging rejects "info", which Celery's -l accepts.
            "level": str(
                values.Value(
                    "WARNING",
                    environ_name="LOGGING_LEVEL_LOGGERS_ROOT",
                    environ_prefix=None,
                )
            ).upper(),
        },
        # get_task_logger() parents the application's task loggers to
        # "celery.task" instead of "core": in the web process, both get the
        # application level.
        "loggers": {
            logger: {
                "level": str(
                    values.Value(
                        "INFO",
                        environ_name="LOGGING_LEVEL_LOGGERS_APP",
                        environ_prefix=None,
                    )
                ).upper(),
            }
            for logger in ("core", "celery.task")
        },
    }
