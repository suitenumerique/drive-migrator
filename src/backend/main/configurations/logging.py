"""Logging configuration shared by the web and Celery processes."""

import structlog
from configurations import values


def drop_celery_task_data(_logger, _method_name, event_dict):
    """Drop the task context that Celery adds to its logs, whose arguments can hold user data."""
    if event_dict["_record"].name.startswith("celery."):
        event_dict.pop("data", None)
    return event_dict


# Hand the structlog events to the standard loggers, so that the LOGGING
# formatter writes them like any other log
structlog.configure(
    processors=[structlog.stdlib.render_to_log_args_and_kwargs],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)


class LoggingMixin:
    """Console logging whose root and app levels are tunable through env vars."""

    # Logging: the web process logs to the console through the root handler.
    # Celery workers keep it (CELERY_WORKER_HIJACK_ROOT_LOGGER) and set the
    # root and task loggers to their -l level.
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "()": structlog.stdlib.ProcessorFormatter,
                "foreign_pre_chain": [
                    structlog.contextvars.merge_contextvars,
                    structlog.stdlib.ExtraAdder(),
                    drop_celery_task_data,
                    structlog.stdlib.add_logger_name,
                    structlog.stdlib.add_log_level,
                    structlog.processors.TimeStamper(fmt="iso", utc=True),
                ],
                "processors": [
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.processors.format_exc_info,
                    structlog.processors.JSONRenderer(default=str),
                ],
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "json",
            },
            "null": {
                "class": "logging.NullHandler",
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
        # application level, as do the request logs of django-structlog.
        "loggers": {
            **{
                logger: {
                    "level": str(
                        values.Value(
                            "INFO",
                            environ_name="LOGGING_LEVEL_LOGGERS_APP",
                            environ_prefix=None,
                        )
                    ).upper(),
                }
                for logger in ("core", "celery.task", "django_structlog")
            },
            # Replaced by the request logs of django-structlog
            "request.summary": {
                "handlers": ["null"],
                "propagate": False,
            },
            "django.request": {
                "handlers": ["null"],
                "propagate": False,
            },
        },
    }
    # Personal data, which the access logs already hold
    DJANGO_STRUCTLOG_IP_LOGGING_ENABLED = False
    DJANGO_STRUCTLOG_CELERY_ENABLED = True
