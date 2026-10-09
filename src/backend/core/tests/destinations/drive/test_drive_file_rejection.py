"""Tests for get_file_rejection()."""

from unittest.mock import MagicMock

import pytest
from requests.exceptions import HTTPError

from core.destinations.drive.drive_backend import FileRejection, get_file_rejection


def _drive_error(status_code, code):
    """Build an HTTPError carrying a standardized Drive error payload."""
    response = MagicMock(status_code=status_code, text="")
    response.json.return_value = {
        "type": "validation_error",
        "errors": [{"code": code, "detail": "Refused.", "attr": None}],
    }
    return HTTPError(f"{status_code} Client Error", response=response)


def _non_json_error(status_code):
    """Build an HTTPError whose body is not JSON (e.g. a proxy or WAF page)."""
    response = MagicMock(
        status_code=status_code, text="<html><body>Request unsuccessful.</body></html>"
    )
    response.json.side_effect = ValueError
    return HTTPError(f"{status_code} Client Error", response=response)


@pytest.mark.parametrize("at_creation", [True, False])
@pytest.mark.parametrize(
    "code",
    [
        "item_create_file_extension_not_allowed",
        "file_type_not_allowed",
        "file_size_exceeded",
        "file_size_mismatch",
    ],
)
def test_drive_refusals_of_the_file_give_their_code(code, at_creation):
    """A 400 refusing the file itself gives its Drive code, at any upload step."""
    rejection = get_file_rejection(_drive_error(400, code), at_creation=at_creation)

    assert rejection == code


def test_non_json_403_at_creation_is_blocked_upstream():
    """Drive API errors are JSON: a 403 that is not comes from in front of Drive."""
    rejection = get_file_rejection(_non_json_error(403), at_creation=True)

    assert rejection is FileRejection.BLOCKED_UPSTREAM


def test_non_json_403_after_creation_is_not_a_refusal_of_the_file():
    """Only the creation carries the file name: a later block is not the file's."""
    assert get_file_rejection(_non_json_error(403)) is None


@pytest.mark.parametrize(
    "error",
    [
        _drive_error(400, "required"),
        _drive_error(403, "file_type_not_allowed"),
        _non_json_error(400),
        _non_json_error(502),
        HTTPError("403 Client Error", response=None),
    ],
    ids=["other-400-code", "json-403", "non-json-400", "non-json-502", "no-response"],
)
def test_other_errors_are_not_refusals_of_the_file(error):
    """Any other error is not a refusal of the file, it must stop the migration."""
    assert get_file_rejection(error, at_creation=True) is None
