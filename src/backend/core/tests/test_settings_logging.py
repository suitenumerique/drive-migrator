"""Tests for the logging configuration shared by every deployed environment."""

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import ANY, Mock, patch
from uuid import uuid4

import pytest
import structlog
from celery.app.log import Logging
from celery.signals import before_task_publish, task_prerun
from rest_framework.test import APIClient

from core import factories
from core.processing.tasks import export

from main.celery_app import app as celery_app

# Applies Base.LOGGING in a fresh process, without touching the test process
# loggers, then gets the loggers the way the application modules do.
_PROBE = """
import json, logging, logging.config, sys
import configurations.importer
configurations.importer.install()
from celery.utils.log import get_task_logger
from main.settings import Base
logging.config.dictConfig(Base.LOGGING)

def handlers_reached(logger):
    # Celery puts NullHandlers on its loggers: they output nothing.
    count = 0
    while logger:
        count += sum(
            not isinstance(handler, logging.NullHandler)
            for handler in logger.handlers
        )
        if not logger.propagate:
            break
        logger = logger.parent
    return count

loggers = {
    "task logger": get_task_logger("core.destinations.drive.drive_backend"),
    "module logger": logging.getLogger("core.sources.resana.token_manager"),
    "library logger": logging.getLogger("some_library"),
}
probe = {
    kind: {
        "info": logger.isEnabledFor(logging.INFO),
        "handlers": handlers_reached(logger),
    }
    for kind, logger in loggers.items()
}
record = logging.LogRecord("core.x", logging.WARNING, "", 0, "hello", None, None)
probe["formatted"] = logging.getLogger().handlers[0].format(record)
sys.stdout.write(json.dumps(probe))
"""


def _probe_base_logging(levels=None):
    """Return how Base.LOGGING treats the application and library loggers,
    with the given LOGGING_LEVEL_* variables only."""
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("LOGGING_LEVEL_")
    } | (levels or {})
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        check=True,
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        text=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.fixture(name="default_probe", scope="module")
def fixture_default_probe():
    """Probe Base.LOGGING once with its default levels."""
    return _probe_base_logging()


def test_base_logging_emits_app_info_logs_once(default_probe):
    """Plain and Celery task loggers of the application log INFO, through one handler."""
    assert default_probe["module logger"] == {"info": True, "handlers": 1}
    assert default_probe["task logger"] == {"info": True, "handlers": 1}


def test_base_logging_keeps_third_party_info_logs_quiet(default_probe):
    """Libraries only log warnings by default."""
    assert default_probe["library logger"]["info"] is False


def test_base_logging_formats_lines_with_date_logger_and_level(default_probe):
    """A console line tells when, where and how severe."""
    line = json.loads(default_probe["formatted"])

    assert line == {
        "event": "hello",
        "level": "warning",
        "logger": "core.x",
        "timestamp": ANY,
    }
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T[\d:.]+Z", line["timestamp"])


def test_base_logging_accepts_lowercase_levels():
    """A lowercase level, as Celery's -l accepts, doesn't break the configuration."""
    probe = _probe_base_logging(
        {"LOGGING_LEVEL_LOGGERS_APP": "info", "LOGGING_LEVEL_LOGGERS_ROOT": "warning"}
    )

    assert probe["module logger"]["info"] is True
    assert probe["library logger"]["info"] is False


def test_logging_core_logger(console_logs):
    """Logs of the core application should be written to the console."""
    logging.getLogger("core.processing.tasks").warning("workspace migration failed")

    assert console_logs == [
        {
            "event": "workspace migration failed",
            "level": "warning",
            "logger": "core.processing.tasks",
        }
    ]


def test_logging_structlog_event(console_logs):
    """A structlog event should be written with its fields and positional arguments."""
    structlog.get_logger("core.processing.tasks").warning(
        "workspace %s migration failed", "ws-1", reason="timeout"
    )

    assert console_logs == [
        {
            "event": "workspace ws-1 migration failed",
            "level": "warning",
            "logger": "core.processing.tasks",
            "reason": "timeout",
        }
    ]


def test_logging_extra_fields(console_logs):
    """The extra fields of a log should be written as text."""
    workspace_id = uuid4()

    logging.getLogger("core.processing.tasks").warning(
        "workspace migration failed", extra={"workspace_id": workspace_id}
    )

    assert console_logs == [
        {
            "event": "workspace migration failed",
            "level": "warning",
            "logger": "core.processing.tasks",
            "workspace_id": str(workspace_id),
        }
    ]


def test_logging_exception(console_logs):
    """An exception should be logged with its traceback."""
    try:
        raise ValueError("unknown workspace")
    except ValueError:
        logging.getLogger("core.processing.tasks").exception(
            "workspace migration failed"
        )

    assert console_logs == [
        {
            "event": "workspace migration failed",
            "level": "error",
            "logger": "core.processing.tasks",
            "exception": ANY,
        }
    ]
    assert console_logs[0]["exception"].startswith("Traceback ")
    assert console_logs[0]["exception"].endswith("\nValueError: unknown workspace")


@pytest.mark.django_db
def test_logging_request(client, console_logs):
    """A request should be logged when it starts and ends."""
    client.get("/api/v1.0/unknown/", HTTP_X_REQUEST_ID="front-42")

    assert console_logs == [
        {
            "event": "request_started",
            "level": "info",
            "logger": "django_structlog.middlewares.request",
            "request": "GET /api/v1.0/unknown/",
            "request_id": "front-42",
            "user_agent": None,
            "user_id": None,
        },
        {
            "event": "request_finished",
            "level": "warning",
            "logger": "django_structlog.middlewares.request",
            "code": 404,
            "duration_ms": ANY,
            "request": "GET /api/v1.0/unknown/",
            "request_id": "front-42",
            "user_id": None,
        },
    ]
    assert isinstance(console_logs[1]["duration_ms"], int)


@pytest.mark.django_db
def test_logging_request_user(console_logs):
    """A request should be logged with the id of its user."""
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)

    client.get("/api/v1.0/users/", HTTP_X_REQUEST_ID="front-42")

    assert console_logs[-1] == {
        "event": "request_finished",
        "level": "info",
        "logger": "django_structlog.middlewares.request",
        "code": 200,
        "duration_ms": ANY,
        "request": "GET /api/v1.0/users/",
        "request_id": "front-42",
        "user_id": str(user.pk),
    }


@pytest.mark.django_db
def test_logging_request_exception(console_logs):
    """A request that raises should be logged with its traceback."""
    user = factories.UserFactory()
    client = APIClient()
    client.raise_request_exception = False
    client.force_login(user)

    with patch(
        "core.api.viewsets.UserViewSet.get_queryset",
        side_effect=ValueError("unknown user"),
    ):
        client.get("/api/v1.0/users/", HTTP_X_REQUEST_ID="front-42")

    assert console_logs[1:] == [
        {
            "event": "request_failed",
            "level": "error",
            "logger": "django_structlog.middlewares.request",
            "code": 500,
            "exception": ANY,
            "request": "GET /api/v1.0/users/",
            "request_id": "front-42",
            "user_id": str(user.pk),
        },
    ]
    assert console_logs[1]["exception"].endswith("\nValueError: unknown user")


def test_logging_heartbeat(client, console_logs):
    """A heartbeat should not be logged."""
    client.get("/__lbheartbeat__/")

    assert console_logs == []


def test_logging_celery_worker(console_logs):
    """Celery workers should write the logs to the console."""
    root = logging.getLogger()
    task_logger = logging.getLogger("celery.task")
    # Setting up the logging of a worker changes these, for the whole test process
    with (
        patch.object(root, "handlers", list(root.handlers)),
        patch.object(root, "level", root.level),
        patch.object(task_logger, "handlers", list(task_logger.handlers)),
        patch.object(task_logger, "level", task_logger.level),
        patch.object(task_logger, "propagate", task_logger.propagate),
        patch.dict(os.environ),
        patch.object(Logging, "_setup", False),
    ):
        celery_app.log.setup_logging_subsystem(loglevel="INFO")
        logging.getLogger("celery.app.trace").info("Task succeeded")

    assert console_logs == [
        {"event": "Task succeeded", "level": "info", "logger": "celery.app.trace"}
    ]


def test_logging_celery_task_arguments(console_logs):
    """Celery logs should be written without the arguments of the tasks."""
    context = {"id": "task-1", "name": "core.processing.tasks.export", "args": "[42]"}

    logging.getLogger("celery.worker.strategy").warning(
        "Task %(name)s[%(id)s] received", context, extra={"data": context}
    )

    assert console_logs == [
        {
            "event": "Task core.processing.tasks.export[task-1] received",
            "level": "warning",
            "logger": "celery.worker.strategy",
        }
    ]


def test_logging_celery_task(console_logs):
    """Logs of a task should carry the request id of the request that sent it."""
    # Start the steps of a worker, which hold its signal receivers
    _worker_steps = [step(Mock()) for step in celery_app.steps["worker"]]
    headers = {}
    structlog.contextvars.bind_contextvars(request_id="front-42")
    before_task_publish.send(sender=export.name, headers=headers, body=None)

    # The worker runs the task in another process, with the message headers
    structlog.contextvars.clear_contextvars()
    export.push_request(**headers)
    try:
        task_prerun.send(
            sender=export, task_id="task-1", task=export, args=(), kwargs={}
        )
        logging.getLogger("core.processing.tasks").info("Starting workspace")
    finally:
        export.pop_request()
        structlog.contextvars.clear_contextvars()

    assert console_logs == [
        {
            "event": "task_started",
            "level": "info",
            "logger": "django_structlog.celery.receivers",
            "request_id": "front-42",
            "task": "core.processing.tasks.export",
            "task_id": "task-1",
        },
        {
            "event": "Starting workspace",
            "level": "info",
            "logger": "core.processing.tasks",
            "request_id": "front-42",
            "task_id": "task-1",
        },
    ]
