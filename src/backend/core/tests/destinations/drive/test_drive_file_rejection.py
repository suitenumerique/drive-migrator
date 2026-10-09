"""Tests for get_file_rejection_code() and is_waf_block()."""

from unittest.mock import MagicMock

import pytest
from requests.exceptions import HTTPError

from core.destinations.drive.drive_backend import get_file_rejection_code, is_waf_block


def _drive_error(status_code, code):
    """Build an HTTPError carrying a standardized Drive error payload."""
    response = MagicMock(status_code=status_code, text="")
    response.json.return_value = {
        "type": "validation_error",
        "errors": [{"code": code, "detail": "Refused.", "attr": None}],
    }
    return HTTPError(f"{status_code} Client Error", response=response)


def _non_json_error():
    """Build a 400 HTTPError whose body is not JSON (e.g. a proxy error page)."""
    response = MagicMock(status_code=400, text="")
    response.json.side_effect = ValueError
    return HTTPError("400 Client Error", response=response)


def _html_error(status_code, body):
    """Build an HTTPError whose body is an HTML page."""
    response = MagicMock(status_code=status_code, text=body)
    response.json.side_effect = ValueError
    return HTTPError(f"{status_code} Client Error", response=response)


_INCAPSULA_PAGE = (
    '<html><body><iframe src="/_Incapsula_Resource?CWUDNSAI=23">'
    "Request unsuccessful. Incapsula incident ID: 985000450107661663"
    "</iframe></body></html>"
)


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
        _html_error(403, _INCAPSULA_PAGE),
    ],
    ids=["other-400-code", "not-a-400", "not-json", "waf-block"],
)
def test_other_errors_are_not_refusals_of_the_file(error):
    """Any other error is not a refusal of the file, it must stop the migration."""
    assert get_file_rejection_code(error) is None


def test_incapsula_403_page_is_a_waf_block():
    """The Incapsula WAF in front of Drive answers a blocked request with a 403 page."""
    assert is_waf_block(_html_error(403, _INCAPSULA_PAGE))


@pytest.mark.parametrize(
    "error",
    [
        _drive_error(403, "permission_denied"),
        _html_error(403, "<html><body>Forbidden</body></html>"),
        _html_error(502, _INCAPSULA_PAGE),
        HTTPError("403 Client Error", response=None),
    ],
    ids=["drive-403", "other-403-page", "incapsula-not-a-403", "no-response"],
)
def test_other_errors_are_not_waf_blocks(error):
    """Only the Incapsula 403 page is a WAF block."""
    assert not is_waf_block(error)
