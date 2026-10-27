"""Fixtures for the tests of the core application."""

import json
import logging
from unittest.mock import patch

import pytest
import structlog


@pytest.fixture(name="console_logs")
def fixture_console_logs():
    """Collect the lines written by the console handler, as formatted in production."""
    handler = logging.getHandlerByName("console")
    lines = []
    # Forget the context that a previous test of this process left behind
    structlog.contextvars.clear_contextvars()

    def emit(record):
        line = json.loads(handler.format(record))
        # The timestamp changes on each run
        del line["timestamp"]
        lines.append(line)

    with patch.object(handler, "emit", side_effect=emit):
        yield lines
