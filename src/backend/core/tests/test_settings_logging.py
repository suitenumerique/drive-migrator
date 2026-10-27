"""Tests for the logging configuration shared by every deployed environment."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import ANY

import pytest

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
