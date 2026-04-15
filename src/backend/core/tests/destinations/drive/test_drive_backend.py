"""Tests for DriveBackend — HTTP client wrapping La Suite Drive API."""

from unittest.mock import MagicMock, mock_open, patch

from core.destinations.drive.drive_backend import DriveBackend

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def test_get_access_token_calls_token_endpoint(settings):
    """get_access_token() performs a client_credentials grant against the OIDC endpoint."""
    settings.DRIVE_OIDC_TOKEN_ENDPOINT = "https://oidc.example.com/token"
    settings.DRIVE_OIDC_CLIENT_ID = "client-id"
    settings.DRIVE_OIDC_CLIENT_SECRET = "client-secret"

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.json.return_value = {"access_token": "tok-abc"}
        mock_requests.post.return_value.raise_for_status = MagicMock()

        backend = DriveBackend()
        token = backend.get_access_token()

    mock_requests.post.assert_called_once_with(
        "https://oidc.example.com/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "scope": "openid email",
        },
        timeout=30,
    )
    assert token == "tok-abc"


# ---------------------------------------------------------------------------
# Folder creation
# ---------------------------------------------------------------------------


def test_create_folder_posts_to_items_endpoint(settings):
    """create_folder() creates a root folder via POST /external_api/v1.0/items/."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.json.return_value = {
            "id": "folder-uuid",
            "type": "folder",
            "title": "My Workspace",
        }
        mock_requests.post.return_value.raise_for_status = MagicMock()

        backend = DriveBackend()
        result = backend.create_folder("My Workspace", token="tok")

    mock_requests.post.assert_called_once_with(
        "https://drive.example.com/external_api/v1.0/items/",
        json={"type": "folder", "title": "My Workspace"},
        headers={"Authorization": "Bearer tok"},
        timeout=30,
    )
    assert result["id"] == "folder-uuid"


def test_create_subfolder_posts_to_children_endpoint(settings):
    """create_subfolder() creates a child folder via POST /external_api/v1.0/items/{parent_id}/children/."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.json.return_value = {
            "id": "child-uuid",
            "type": "folder",
            "title": "Subfolder",
        }
        mock_requests.post.return_value.raise_for_status = MagicMock()

        backend = DriveBackend()
        result = backend.create_subfolder(
            "Subfolder", parent_id="parent-uuid", token="tok"
        )

    mock_requests.post.assert_called_once_with(
        "https://drive.example.com/external_api/v1.0/items/parent-uuid/children/",
        json={"type": "folder", "title": "Subfolder"},
        headers={"Authorization": "Bearer tok"},
        timeout=30,
    )
    assert result["id"] == "child-uuid"


# ---------------------------------------------------------------------------
# File upload (3-step)
# ---------------------------------------------------------------------------


def test_create_file_item_posts_to_children_endpoint(settings):
    """create_file_item() creates a file item and returns the S3 policy URL."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.json.return_value = {
            "id": "file-uuid",
            "type": "file",
            "filename": "doc.pdf",
            "upload_state": "pending",
            "policy": "https://s3.example.com/bucket/doc.pdf?sig=abc",
        }
        mock_requests.post.return_value.raise_for_status = MagicMock()

        backend = DriveBackend()
        result = backend.create_file_item(
            "doc.pdf", parent_id="folder-uuid", token="tok"
        )

    mock_requests.post.assert_called_once_with(
        "https://drive.example.com/external_api/v1.0/items/folder-uuid/children/",
        json={"type": "file", "filename": "doc.pdf"},
        headers={"Authorization": "Bearer tok"},
        timeout=30,
    )
    assert result["id"] == "file-uuid"
    assert result["policy"] == "https://s3.example.com/bucket/doc.pdf?sig=abc"


def test_upload_to_s3_puts_file_content():
    """upload_to_s3() sends a PUT request with the file content to the presigned URL."""
    policy_url = "https://s3.example.com/bucket/doc.pdf?sig=abc"
    file_content = b"binary content"

    with (
        patch("core.destinations.drive.drive_backend.requests") as mock_requests,
        patch("builtins.open", mock_open(read_data=file_content)),
    ):
        mock_requests.put.return_value.raise_for_status = MagicMock()

        backend = DriveBackend()
        backend.upload_to_s3(policy_url, "/tmp/workspace/doc.pdf")

    mock_requests.put.assert_called_once_with(
        policy_url,
        data=file_content,
        headers={"x-amz-acl": "private"},
        timeout=300,
    )


def test_notify_upload_ended_calls_endpoint(settings):
    """notify_upload_ended() calls POST /external_api/v1.0/items/{id}/upload-ended/."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.raise_for_status = MagicMock()

        backend = DriveBackend()
        backend.notify_upload_ended("file-uuid", token="tok")

    mock_requests.post.assert_called_once_with(
        "https://drive.example.com/external_api/v1.0/items/file-uuid/upload-ended/",
        headers={"Authorization": "Bearer tok"},
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Sharing
# ---------------------------------------------------------------------------


def test_find_user_by_email_returns_user_when_found_paginated(settings):
    """find_user_by_email() returns the user dict from a paginated response."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.get.return_value.json.return_value = {
            "results": [{"id": "user-uuid", "email": "alice@example.com"}]
        }
        mock_requests.get.return_value.raise_for_status = MagicMock()

        backend = DriveBackend()
        result = backend.find_user_by_email("alice@example.com", token="tok")

    mock_requests.get.assert_called_once_with(
        "https://drive.example.com/api/v1.0/users/",
        params={"q": "alice@example.com"},
        headers={"Authorization": "Bearer tok"},
        timeout=30,
    )
    assert result == {"id": "user-uuid", "email": "alice@example.com"}


def test_find_user_by_email_returns_user_when_found_list(settings):
    """find_user_by_email() returns the user dict from a flat list response."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.get.return_value.json.return_value = [
            {"id": "user-uuid", "email": "alice@example.com"}
        ]
        mock_requests.get.return_value.raise_for_status = MagicMock()

        backend = DriveBackend()
        result = backend.find_user_by_email("alice@example.com", token="tok")

    assert result == {"id": "user-uuid", "email": "alice@example.com"}


def test_find_user_by_email_returns_none_when_not_found(settings):
    """find_user_by_email() returns None when Drive returns an empty result set."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.get.return_value.json.return_value = {"results": []}
        mock_requests.get.return_value.raise_for_status = MagicMock()

        backend = DriveBackend()
        result = backend.find_user_by_email("unknown@example.com", token="tok")

    assert result is None


def test_share_with_user_posts_to_accesses_endpoint(settings):
    """share_with_user() creates an access for an existing Drive user."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.raise_for_status = MagicMock()

        backend = DriveBackend()
        backend.share_with_user("item-uuid", "user-uuid", token="tok")

    mock_requests.post.assert_called_once_with(
        "https://drive.example.com/external_api/v1.0/items/item-uuid/accesses/",
        json={"user_id": "user-uuid", "role": "owner"},
        headers={"Authorization": "Bearer tok"},
        timeout=30,
    )


def test_invite_by_email_posts_to_invitations_endpoint(settings):
    """invite_by_email() sends an invitation to a user not yet registered in Drive."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.raise_for_status = MagicMock()

        backend = DriveBackend()
        backend.invite_by_email("item-uuid", "new@example.com", token="tok")

    mock_requests.post.assert_called_once_with(
        "https://drive.example.com/external_api/v1.0/items/item-uuid/invitations/",
        json={"email": "new@example.com", "role": "owner"},
        headers={"Authorization": "Bearer tok"},
        timeout=30,
    )
