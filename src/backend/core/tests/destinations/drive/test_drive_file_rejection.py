"""Tests for get_file_rejection_code()."""

from unittest.mock import MagicMock

import pytest
from requests.exceptions import HTTPError

from core.destinations.drive.drive_backend import get_file_rejection_code


def _drive_error(status_code, code):
    """Build an HTTPError carrying a standardized Drive error payload."""
    response = MagicMock(status_code=status_code)
    response.json.return_value = {
        "type": "validation_error",
        "errors": [{"code": code, "detail": "Refused.", "attr": None}],
    }
    return HTTPError(f"{status_code} Client Error", response=response)


def _non_json_error():
    """Build a 400 HTTPError whose body is not JSON (e.g. a proxy error page)."""
    response = MagicMock(status_code=400)
    response.json.side_effect = ValueError
    return HTTPError("400 Client Error", response=response)


@pytest.mark.parametrize(
    "code",
    [
        "item_create_file_extension_not_allowed",
        "file_type_not_allowed",
        "file_size_exceeded",
        "file_size_mismatch",
    ],
)
def test_drive_refusals_of_the_file_give_their_code(code):
    """A 400 refusing the file itself gives its Drive code."""
    assert get_file_rejection_code(_drive_error(400, code)) == code


@pytest.mark.parametrize(
    "error",
    [
        _drive_error(400, "required"),
        _drive_error(403, "file_type_not_allowed"),
        _non_json_error(),
    ],
    ids=["other-400-code", "not-a-400", "not-json"],
)
def test_other_errors_are_not_refusals_of_the_file(error):
    """Any other error is not a refusal of the file, it must stop the migration."""
    assert get_file_rejection_code(error) is None
