"""Tests for the Drive client calls used by the migration integrity check."""

# pylint: disable=protected-access

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.utils import timezone

import pytest
from cryptography.fernet import Fernet

from core.destinations.drive.drive_backend import (
    DriveServiceAccountBackend,
    DriveUserTokenBackend,
)
from core.encryption import encrypt_token


@pytest.fixture(autouse=True)
def set_encryption_key(settings):
    settings.OIDC_TOKENS_ENCRYPTION_KEY = Fernet.generate_key().decode()


# ---------------------------------------------------------------------------
# Integrity check helpers: explicit item id, get_item, list_children
# ---------------------------------------------------------------------------


def _service_account_backend(settings):
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"
    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"
    backend._token_expires_at = timezone.now() + timedelta(hours=1)
    return backend


def _json_response(payload, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    return response


def test_create_file_item_uses_given_item_id(settings):
    """create_file_item() sends the caller-provided id instead of generating one."""
    backend = _service_account_backend(settings)

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value = _json_response(
            {"id": "given-id", "policy": "https://s3.example.com/x"}
        )
        backend.create_file_item(
            "doc.pdf", parent_id="folder-uuid", size=7, item_id="given-id"
        )

    assert mock_requests.post.call_args.kwargs["json"] == {
        "id": "given-id",
        "type": "file",
        "filename": "doc.pdf",
        "size": 7,
    }


def test_get_item_returns_item(settings):
    """get_item() returns the item payload from GET /items/{id}/."""
    backend = _service_account_backend(settings)

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.get.return_value = _json_response(
            {"id": "item-uuid", "upload_state": "pending"}
        )
        result = backend.get_item("item-uuid")

    mock_requests.get.assert_called_once_with(
        "https://drive.example.com/external_api/v1.0/items/item-uuid/",
        headers={"Authorization": "Bearer tok"},
        timeout=30,
    )
    assert result == {"id": "item-uuid", "upload_state": "pending"}


def test_get_item_returns_none_on_404(settings):
    """get_item() returns None when Drive does not know the item."""
    backend = _service_account_backend(settings)

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.get.return_value = _json_response({}, status_code=404)
        result = backend.get_item("missing-uuid")

    assert result is None


def test_list_children_follows_pagination(settings):
    """list_children() walks every page through the "next" link."""
    backend = _service_account_backend(settings)
    next_url = (
        "https://drive.example.com/external_api/v1.0/items/folder-uuid/children/"
        "?page=2&page_size=200"
    )

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.get.side_effect = [
            _json_response({"results": [{"id": "a"}, {"id": "b"}], "next": next_url}),
            _json_response({"results": [{"id": "c"}], "next": None}),
        ]
        result = backend.list_children("folder-uuid")

    assert result == [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    first_call, second_call = mock_requests.get.call_args_list
    assert first_call.args == (
        "https://drive.example.com/external_api/v1.0/items/folder-uuid/children/",
    )
    assert first_call.kwargs["params"] == {"page_size": 200}
    assert second_call.args == (next_url,)
    assert second_call.kwargs["params"] is None


def test_user_token_list_children_uses_api_v1(settings):
    """list_children() uses /api/v1.0/ in user_token mode."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"
    user = MagicMock()
    user.oidc_access_token = encrypt_token("user-tok")
    user.oidc_token_expires_at = timezone.now() + timedelta(hours=1)

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.get.return_value = _json_response({"results": [], "next": None})
        DriveUserTokenBackend(user).list_children("folder-uuid")

    assert mock_requests.get.call_args.args == (
        "https://drive.example.com/api/v1.0/items/folder-uuid/children/",
    )
