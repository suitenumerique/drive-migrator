"""Tests for core.utils."""

import os
from datetime import datetime
from datetime import timezone as dt_timezone
from unittest.mock import MagicMock

import pytest
import requests
from freezegun import freeze_time

from core.utils import (
    describe_access_expiry,
    describe_expiry,
    describe_oauth_error,
    describe_request_error,
    ensure_inside_directory,
    sanitize_path_component,
)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("report", "report"),
        ("GT/Socle", "GT-Socle"),
        ("../escape", "..-escape"),
        ("..", ".."),
        (".", "."),
        ("v1.2", "v1.2"),
    ],
)
def test_sanitize_path_component(name, expected):
    """A name never splits into sub-paths."""
    assert sanitize_path_component(name) == expected


@pytest.mark.parametrize("name", ["report", "v1.2", "...", ".hidden"])
def test_ensure_inside_directory_accepts_a_child(tmp_path, name):
    """A path designating an entry of the directory is accepted."""
    ensure_inside_directory(os.path.join(tmp_path, name), tmp_path)


@pytest.mark.parametrize("name", [".", "..", "../sibling", "sub/../.."])
def test_ensure_inside_directory_rejects_a_path_escaping_the_directory(tmp_path, name):
    """A path resolving to the directory itself or above it is rejected."""
    with pytest.raises(ValueError):
        ensure_inside_directory(os.path.join(tmp_path, name), tmp_path)


def test_ensure_inside_directory_rejects_a_symlink_escaping_the_directory(tmp_path):
    """A symlink pointing outside of the directory is rejected."""
    directory = tmp_path / "workspace"
    directory.mkdir()
    (directory / "link").symlink_to(tmp_path)

    with pytest.raises(ValueError):
        ensure_inside_directory(directory / "link" / "x.txt", directory)


def test_ensure_inside_directory_accepts_a_directory_behind_a_symlink(tmp_path):
    """The directory itself may be reached through a symlink."""
    (tmp_path / "real").mkdir()
    (tmp_path / "alias").symlink_to(tmp_path / "real")

    ensure_inside_directory(tmp_path / "alias" / "x.txt", tmp_path / "alias")


@freeze_time("2026-10-15 15:18:08")
@pytest.mark.parametrize(
    "expires_at,expected",
    [
        (None, "no expiry"),
        (
            datetime(2026, 10, 15, 15, 20, 0, tzinfo=dt_timezone.utc),
            "expires at 2026-10-15T15:20:00+00:00 (in 112s)",
        ),
        (
            datetime(2026, 10, 15, 15, 10, 0, tzinfo=dt_timezone.utc),
            "expired since 2026-10-15T15:10:00+00:00 (488s ago)",
        ),
    ],
)
def test_describe_expiry(expires_at, expected):
    """An expiry date is described relative to now."""
    assert describe_expiry(expires_at) == expected


def _http_error(status_code, json_body=None, json_error=None):
    """Build an HTTPError whose response answers json() with a body or an error."""
    response = MagicMock(status_code=status_code)
    response.json.return_value = json_body
    response.json.side_effect = json_error
    return requests.HTTPError(f"{status_code} Error", response=response)


@pytest.mark.parametrize(
    "error,expected",
    [
        (
            _http_error(
                400,
                {
                    "error": "invalid_grant",
                    "error_description": "Offline session not active",
                },
            ),
            "400 invalid_grant (Offline session not active)",
        ),
        (_http_error(400, {"error": "invalid_grant"}), "400 invalid_grant"),
        (_http_error(400, {}), "400 without OAuth error"),
        (_http_error(502, ["not", "an", "object"]), "502 without OAuth error"),
        (_http_error(502, "plain string"), "502 without OAuth error"),
        (_http_error(503, json_error=ValueError), "503 without OAuth error"),
        (requests.HTTPError("connection reset"), "HTTPError without response"),
        (
            _http_error(400, {"error": "invalid\ngrant " * 20}),
            "400 " + ("invalid grant " * 20)[:64],
        ),
        (_http_error(400, {"error": {"code": 1}}), "400 {'code': 1}"),
        (
            _http_error(
                400,
                {"error": "invalid_grant", "error_description": "line\nbreak " * 30},
            ),
            "400 invalid_grant (" + ("line break " * 30)[:200] + ")",
        ),
    ],
)
def test_describe_oauth_error(error, expected):
    """Any token endpoint answer is described without raising."""
    assert describe_oauth_error(error) == expected


@freeze_time("2026-10-15 15:18:08")
@pytest.mark.parametrize(
    "expires_at,expected",
    [
        (None, "expiry unknown (will be refreshed)"),
        (
            datetime(2026, 10, 15, 15, 20, 0, tzinfo=dt_timezone.utc),
            "expires at 2026-10-15T15:20:00+00:00 (in 112s)",
        ),
    ],
)
def test_describe_access_expiry(expires_at, expected):
    """An access token without expiry date is refreshed, not trusted."""
    assert describe_access_expiry(expires_at) == expected


@pytest.mark.parametrize(
    "error,expected",
    [
        (
            requests.HTTPError("401 for url", response=MagicMock(status_code=401)),
            "HTTPError 401",
        ),
        (requests.HTTPError("no response"), "HTTPError"),
        (requests.Timeout("timed out after 30s"), "Timeout"),
        (ValueError("Bridge response missing fields"), "ValueError"),
    ],
)
def test_describe_request_error(error, expected):
    """A request error is described by its type and status, never its message."""
    assert describe_request_error(error) == expected
