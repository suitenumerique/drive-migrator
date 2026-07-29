"""Tests for DriveServiceAccountBackend and DriveUserTokenBackend."""

from datetime import timedelta
from unittest.mock import MagicMock, mock_open, patch

from django.utils import timezone

import pytest
from cryptography.fernet import Fernet

from core.destinations.drive.drive_backend import (
    DriveServiceAccountBackend,
    DriveUserTokenBackend,
    user_has_usable_drive_token,
)
from core.encryption import decrypt_token, encrypt_token

TEST_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def set_encryption_key(settings):
    settings.OIDC_TOKENS_ENCRYPTION_KEY = TEST_KEY


# ---------------------------------------------------------------------------
# DriveServiceAccountBackend — token management
# ---------------------------------------------------------------------------


def test_service_account_get_token_calls_client_credentials(settings):
    """_get_token() fetches a token via client_credentials when none is cached."""
    settings.DRIVE_OIDC_TOKEN_ENDPOINT = "https://oidc.example.com/token"
    settings.DRIVE_OIDC_CLIENT_ID = "client-id"
    settings.DRIVE_OIDC_CLIENT_SECRET = "client-secret"

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.json.return_value = {
            "access_token": "tok-abc",
            "expires_in": 3600,
        }
        mock_requests.post.return_value.raise_for_status = MagicMock()

        backend = DriveServiceAccountBackend()
        token = backend._get_token()

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


def test_service_account_get_token_uses_cache_when_valid(settings):
    """_get_token() reuses the cached token when it has not yet expired."""
    settings.DRIVE_OIDC_TOKEN_ENDPOINT = "https://oidc.example.com/token"
    settings.DRIVE_OIDC_CLIENT_ID = "client-id"
    settings.DRIVE_OIDC_CLIENT_SECRET = "client-secret"

    backend = DriveServiceAccountBackend()
    backend._access_token = "cached-tok"
    backend._token_expires_at = timezone.now() + timedelta(hours=1)

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        token = backend._get_token()

    mock_requests.post.assert_not_called()
    assert token == "cached-tok"


def test_service_account_get_token_refreshes_when_expired(settings):
    """_get_token() refreshes via client_credentials when the cached token is expired."""
    settings.DRIVE_OIDC_TOKEN_ENDPOINT = "https://oidc.example.com/token"
    settings.DRIVE_OIDC_CLIENT_ID = "client-id"
    settings.DRIVE_OIDC_CLIENT_SECRET = "client-secret"

    backend = DriveServiceAccountBackend()
    backend._access_token = "old-tok"
    backend._token_expires_at = timezone.now() - timedelta(seconds=1)

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.json.return_value = {
            "access_token": "new-tok",
            "expires_in": 3600,
        }
        mock_requests.post.return_value.raise_for_status = MagicMock()
        token = backend._get_token()

    assert token == "new-tok"
    mock_requests.post.assert_called_once()


def test_service_account_get_token_refreshes_within_buffer(settings):
    """_get_token() refreshes proactively when the token expires within 10 seconds."""
    settings.DRIVE_OIDC_TOKEN_ENDPOINT = "https://oidc.example.com/token"
    settings.DRIVE_OIDC_CLIENT_ID = "client-id"
    settings.DRIVE_OIDC_CLIENT_SECRET = "client-secret"

    backend = DriveServiceAccountBackend()
    backend._access_token = "almost-expired-tok"
    backend._token_expires_at = timezone.now() + timedelta(seconds=5)

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.json.return_value = {
            "access_token": "refreshed-tok",
            "expires_in": 3600,
        }
        mock_requests.post.return_value.raise_for_status = MagicMock()
        token = backend._get_token()

    assert token == "refreshed-tok"


# ---------------------------------------------------------------------------
# DriveServiceAccountBackend — folder operations
# ---------------------------------------------------------------------------


def test_service_account_create_folder_uses_external_api(settings):
    """create_folder() posts to /external_api/v1.0/items/ with the internal token."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"
    backend._token_expires_at = timezone.now() + timedelta(hours=1)

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.json.return_value = {"id": "folder-uuid"}
        mock_requests.post.return_value.raise_for_status = MagicMock()
        result = backend.create_folder("My Workspace")

    mock_requests.post.assert_called_once_with(
        "https://drive.example.com/external_api/v1.0/items/",
        json={"type": "folder", "title": "My Workspace"},
        headers={"Authorization": "Bearer tok"},
        timeout=30,
    )
    assert result["id"] == "folder-uuid"


def test_service_account_create_subfolder(settings):
    """create_subfolder() posts to /external_api/v1.0/items/{parent_id}/children/."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"
    backend._token_expires_at = timezone.now() + timedelta(hours=1)

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.json.return_value = {"id": "child-uuid"}
        mock_requests.post.return_value.raise_for_status = MagicMock()
        result = backend.create_subfolder("docs", parent_id="parent-uuid")

    mock_requests.post.assert_called_once_with(
        "https://drive.example.com/external_api/v1.0/items/parent-uuid/children/",
        json={"type": "folder", "title": "docs"},
        headers={"Authorization": "Bearer tok"},
        timeout=30,
    )
    assert result["id"] == "child-uuid"


# ---------------------------------------------------------------------------
# DriveServiceAccountBackend — file upload (3-step)
# ---------------------------------------------------------------------------


def test_service_account_create_file_item(settings):
    """create_file_item() posts to /external_api/v1.0/items/{parent}/children/."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"
    backend._token_expires_at = timezone.now() + timedelta(hours=1)

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.json.return_value = {
            "id": "file-uuid",
            "policy": "https://s3.example.com/file.pdf?sig=x",
        }
        mock_requests.post.return_value.raise_for_status = MagicMock()
        result = backend.create_file_item("doc.pdf", parent_id="folder-uuid")

    mock_requests.post.assert_called_once_with(
        "https://drive.example.com/external_api/v1.0/items/folder-uuid/children/",
        json={"type": "file", "filename": "doc.pdf"},
        headers={"Authorization": "Bearer tok"},
        timeout=30,
    )
    assert result["id"] == "file-uuid"
    assert result["policy"] == "https://s3.example.com/file.pdf?sig=x"


def test_service_account_upload_to_s3_puts_file_content():
    """upload_to_s3() sends a PUT with file content to the presigned URL (no Drive token)."""
    file_content = b"binary content"
    policy_url = "https://s3.example.com/file.pdf?sig=x"

    with (
        patch("core.destinations.drive.drive_backend.requests") as mock_requests,
        patch("builtins.open", mock_open(read_data=file_content)),
    ):
        mock_requests.put.return_value.raise_for_status = MagicMock()
        DriveServiceAccountBackend().upload_to_s3(policy_url, "/tmp/doc.pdf")

    mock_requests.put.assert_called_once_with(
        policy_url,
        data=file_content,
        headers={"x-amz-acl": "private"},
        timeout=300,
    )


def test_service_account_notify_upload_ended(settings):
    """notify_upload_ended() posts to /external_api/v1.0/items/{id}/upload-ended/."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"
    backend._token_expires_at = timezone.now() + timedelta(hours=1)

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.raise_for_status = MagicMock()
        backend.notify_upload_ended("file-uuid")

    mock_requests.post.assert_called_once_with(
        "https://drive.example.com/external_api/v1.0/items/file-uuid/upload-ended/",
        headers={"Authorization": "Bearer tok"},
        timeout=30,
    )


# ---------------------------------------------------------------------------
# DriveServiceAccountBackend — sharing
# ---------------------------------------------------------------------------


def test_service_account_find_user_by_email_paginated(settings):
    """find_user_by_email() always uses /api/v1.0/users/ regardless of auth mode."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"
    backend._token_expires_at = timezone.now() + timedelta(hours=1)

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.get.return_value.json.return_value = {
            "results": [{"id": "user-uuid", "email": "alice@example.com"}]
        }
        mock_requests.get.return_value.raise_for_status = MagicMock()
        result = backend.find_user_by_email("alice@example.com")

    mock_requests.get.assert_called_once_with(
        "https://drive.example.com/api/v1.0/users/",
        params={"q": "alice@example.com"},
        headers={"Authorization": "Bearer tok"},
        timeout=30,
    )
    assert result == {"id": "user-uuid", "email": "alice@example.com"}


def test_service_account_find_user_by_email_flat_list(settings):
    """find_user_by_email() handles a flat list response."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"
    backend._token_expires_at = timezone.now() + timedelta(hours=1)

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.get.return_value.json.return_value = [
            {"id": "user-uuid", "email": "alice@example.com"}
        ]
        mock_requests.get.return_value.raise_for_status = MagicMock()
        result = backend.find_user_by_email("alice@example.com")

    assert result == {"id": "user-uuid", "email": "alice@example.com"}


def test_service_account_find_user_by_email_not_found(settings):
    """find_user_by_email() returns None when Drive returns an empty result set."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"
    backend._token_expires_at = timezone.now() + timedelta(hours=1)

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.get.return_value.json.return_value = {"results": []}
        mock_requests.get.return_value.raise_for_status = MagicMock()
        result = backend.find_user_by_email("unknown@example.com")

    assert result is None


def test_service_account_share_with_user(settings):
    """share_with_user() posts to /external_api/v1.0/items/{id}/accesses/."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"
    backend._token_expires_at = timezone.now() + timedelta(hours=1)

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.raise_for_status = MagicMock()
        backend.share_with_user("item-uuid", "user-uuid")

    mock_requests.post.assert_called_once_with(
        "https://drive.example.com/external_api/v1.0/items/item-uuid/accesses/",
        json={"user_id": "user-uuid", "role": "owner"},
        headers={"Authorization": "Bearer tok"},
        timeout=30,
    )


def test_service_account_invite_by_email(settings):
    """invite_by_email() posts to /external_api/v1.0/items/{id}/invitations/."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    backend = DriveServiceAccountBackend()
    backend._access_token = "tok"
    backend._token_expires_at = timezone.now() + timedelta(hours=1)

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.raise_for_status = MagicMock()
        backend.invite_by_email("item-uuid", "new@example.com")

    mock_requests.post.assert_called_once_with(
        "https://drive.example.com/external_api/v1.0/items/item-uuid/invitations/",
        json={"email": "new@example.com", "role": "owner"},
        headers={"Authorization": "Bearer tok"},
        timeout=30,
    )


# ---------------------------------------------------------------------------
# DriveUserTokenBackend — token management
# ---------------------------------------------------------------------------


def _make_user(
    access_token="initial-tok",
    refresh_token="refresh-tok",
    expires_at=None,
    email="user@example.com",
):
    """Build a mock user with tokens already encrypted, as stored in the real DB."""
    user = MagicMock()
    user.email = email
    user.oidc_access_token = encrypt_token(access_token) if access_token else ""
    user.oidc_refresh_token = encrypt_token(refresh_token) if refresh_token else ""
    user.oidc_token_expires_at = expires_at
    return user


def test_user_token_uses_stored_access_token_when_valid():
    """_get_token() returns the user's stored access token when not expired."""
    user = _make_user(
        access_token="stored-tok",
        expires_at=timezone.now() + timedelta(hours=1),
    )

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        token = DriveUserTokenBackend(user)._get_token()

    mock_requests.post.assert_not_called()
    assert token == "stored-tok"


def test_user_token_refreshes_when_access_token_expired(settings):
    """_get_token() uses the refresh_token when the stored access token is expired."""
    settings.OIDC_OP_TOKEN_ENDPOINT = "https://oidc.example.com/token"
    settings.OIDC_RP_CLIENT_ID = "osmose-client"
    settings.OIDC_RP_CLIENT_SECRET = "osmose-secret"

    user = _make_user(
        access_token="expired-tok",
        refresh_token="valid-refresh-tok",
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.json.return_value = {
            "access_token": "new-tok",
            "expires_in": 60,
        }
        mock_requests.post.return_value.raise_for_status = MagicMock()
        token = DriveUserTokenBackend(user)._get_token()

    mock_requests.post.assert_called_once_with(
        "https://oidc.example.com/token",
        data={
            "grant_type": "refresh_token",
            "client_id": "osmose-client",
            "client_secret": "osmose-secret",
            "refresh_token": "valid-refresh-tok",
        },
        timeout=30,
    )
    assert token == "new-tok"


def test_user_token_refresh_persists_new_tokens_to_user(settings):
    """After refresh, updated tokens are saved back to the user model."""
    settings.OIDC_OP_TOKEN_ENDPOINT = "https://oidc.example.com/token"
    settings.OIDC_RP_CLIENT_ID = "osmose-client"
    settings.OIDC_RP_CLIENT_SECRET = "osmose-secret"

    user = _make_user(
        access_token="expired-tok",
        refresh_token="old-refresh",
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.json.return_value = {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 60,
        }
        mock_requests.post.return_value.raise_for_status = MagicMock()
        DriveUserTokenBackend(user)._get_token()

    assert decrypt_token(user.oidc_access_token) == "new-access"
    assert decrypt_token(user.oidc_refresh_token) == "new-refresh"
    assert user.oidc_token_expires_at is not None
    user.save.assert_called_once_with(
        update_fields=[
            "oidc_access_token",
            "oidc_refresh_token",
            "oidc_token_expires_at",
            "updated_at",
        ]
    )


def test_user_token_refresh_keeps_old_refresh_token_when_none_returned(settings):
    """If the OIDC response has no new refresh_token, the existing one is kept."""
    settings.OIDC_OP_TOKEN_ENDPOINT = "https://oidc.example.com/token"
    settings.OIDC_RP_CLIENT_ID = "osmose-client"
    settings.OIDC_RP_CLIENT_SECRET = "osmose-secret"

    user = _make_user(
        access_token="expired-tok",
        refresh_token="keep-this-refresh",
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.json.return_value = {
            "access_token": "new-access",
            "expires_in": 60,
            # no refresh_token in response
        }
        mock_requests.post.return_value.raise_for_status = MagicMock()
        DriveUserTokenBackend(user)._get_token()

    assert decrypt_token(user.oidc_refresh_token) == "keep-this-refresh"


def test_user_token_raises_when_no_refresh_token():
    """_get_token() raises RuntimeError when the token is expired and no refresh_token is stored."""
    user = _make_user(
        access_token="expired-tok",
        refresh_token="",
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    with pytest.raises(RuntimeError, match="No refresh token stored"):
        DriveUserTokenBackend(user)._get_token()


# ---------------------------------------------------------------------------
# DriveUserTokenBackend — uses /api/v1.0/ endpoints
# ---------------------------------------------------------------------------


def test_user_token_create_folder_uses_api_v1(settings):
    """create_folder() uses /api/v1.0/ (not /external_api/) when in user_token mode."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    user = _make_user(expires_at=timezone.now() + timedelta(hours=1))

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.json.return_value = {"id": "folder-uuid"}
        mock_requests.post.return_value.raise_for_status = MagicMock()
        DriveUserTokenBackend(user).create_folder("My Workspace")

    mock_requests.post.assert_called_once_with(
        "https://drive.example.com/api/v1.0/items/",
        json={"type": "folder", "title": "My Workspace"},
        headers={"Authorization": "Bearer initial-tok"},
        timeout=30,
    )


def test_user_token_create_subfolder_uses_api_v1(settings):
    """create_subfolder() uses /api/v1.0/items/{parent_id}/children/."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    user = _make_user(expires_at=timezone.now() + timedelta(hours=1))

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.json.return_value = {"id": "child-uuid"}
        mock_requests.post.return_value.raise_for_status = MagicMock()
        DriveUserTokenBackend(user).create_subfolder("docs", parent_id="parent-uuid")

    mock_requests.post.assert_called_once_with(
        "https://drive.example.com/api/v1.0/items/parent-uuid/children/",
        json={"type": "folder", "title": "docs"},
        headers={"Authorization": "Bearer initial-tok"},
        timeout=30,
    )


def test_user_token_create_file_item_uses_api_v1(settings):
    """create_file_item() uses /api/v1.0/items/{parent_id}/children/."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    user = _make_user(expires_at=timezone.now() + timedelta(hours=1))

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.json.return_value = {
            "id": "file-uuid",
            "policy": "https://s3.example.com/file.pdf?sig=x",
        }
        mock_requests.post.return_value.raise_for_status = MagicMock()
        result = DriveUserTokenBackend(user).create_file_item(
            "doc.pdf", parent_id="folder-uuid"
        )

    mock_requests.post.assert_called_once_with(
        "https://drive.example.com/api/v1.0/items/folder-uuid/children/",
        json={"type": "file", "filename": "doc.pdf"},
        headers={"Authorization": "Bearer initial-tok"},
        timeout=30,
    )
    assert result["policy"] == "https://s3.example.com/file.pdf?sig=x"


def test_user_token_notify_upload_ended_uses_api_v1(settings):
    """notify_upload_ended() uses /api/v1.0/items/{id}/upload-ended/."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    user = _make_user(expires_at=timezone.now() + timedelta(hours=1))

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.raise_for_status = MagicMock()
        DriveUserTokenBackend(user).notify_upload_ended("file-uuid")

    mock_requests.post.assert_called_once_with(
        "https://drive.example.com/api/v1.0/items/file-uuid/upload-ended/",
        headers={"Authorization": "Bearer initial-tok"},
        timeout=30,
    )


def test_user_token_share_with_user_uses_api_v1(settings):
    """share_with_user() uses /api/v1.0/items/{id}/accesses/."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    user = _make_user(expires_at=timezone.now() + timedelta(hours=1))

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.raise_for_status = MagicMock()
        DriveUserTokenBackend(user).share_with_user("item-uuid", "user-uuid")

    mock_requests.post.assert_called_once_with(
        "https://drive.example.com/api/v1.0/items/item-uuid/accesses/",
        json={"user_id": "user-uuid", "role": "owner"},
        headers={"Authorization": "Bearer initial-tok"},
        timeout=30,
    )


def test_user_token_invite_by_email_uses_api_v1(settings):
    """invite_by_email() uses /api/v1.0/items/{id}/invitations/."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    user = _make_user(expires_at=timezone.now() + timedelta(hours=1))

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.raise_for_status = MagicMock()
        DriveUserTokenBackend(user).invite_by_email("item-uuid", "new@example.com")

    mock_requests.post.assert_called_once_with(
        "https://drive.example.com/api/v1.0/items/item-uuid/invitations/",
        json={"email": "new@example.com", "role": "owner"},
        headers={"Authorization": "Bearer initial-tok"},
        timeout=30,
    )


def test_user_token_find_user_by_email_always_uses_api_v1(settings):
    """find_user_by_email() always uses /api/v1.0/users/ (same as service account mode)."""
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"

    user = _make_user(expires_at=timezone.now() + timedelta(hours=1))

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.get.return_value.json.return_value = {
            "results": [{"id": "user-uuid"}]
        }
        mock_requests.get.return_value.raise_for_status = MagicMock()
        DriveUserTokenBackend(user).find_user_by_email("alice@example.com")

    mock_requests.get.assert_called_once_with(
        "https://drive.example.com/api/v1.0/users/",
        params={"q": "alice@example.com"},
        headers={"Authorization": "Bearer initial-tok"},
        timeout=30,
    )


# ---------------------------------------------------------------------------
# DriveUserTokenBackend — token encryption at rest
# ---------------------------------------------------------------------------


def test_user_token_decrypts_stored_access_token_on_init():
    """DriveUserTokenBackend decrypts the stored access token when initialised."""
    user = _make_user(
        access_token="secret-access",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    backend = DriveUserTokenBackend(user)
    assert backend._access_token == "secret-access"


def test_user_token_get_token_returns_decrypted_plaintext():
    """_get_token() returns the plaintext token usable in HTTP headers."""
    user = _make_user(
        access_token="secret-access",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    with patch("core.destinations.drive.drive_backend.requests"):
        token = DriveUserTokenBackend(user)._get_token()
    assert token == "secret-access"


def test_user_token_refresh_stores_access_token_encrypted(settings):
    """After refresh, the new access_token is stored encrypted in the user model."""
    settings.OIDC_OP_TOKEN_ENDPOINT = "https://oidc.example.com/token"
    settings.OIDC_RP_CLIENT_ID = "osmose-client"
    settings.OIDC_RP_CLIENT_SECRET = "osmose-secret"

    user = _make_user(
        access_token="expired-tok",
        refresh_token="valid-refresh",
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.json.return_value = {
            "access_token": "new-plaintext-access",
            "expires_in": 60,
        }
        mock_requests.post.return_value.raise_for_status = MagicMock()
        DriveUserTokenBackend(user)._get_token()

    assert user.oidc_access_token != "new-plaintext-access"
    assert decrypt_token(user.oidc_access_token) == "new-plaintext-access"


def test_user_token_refresh_stores_refresh_token_encrypted(settings):
    """After refresh, the new refresh_token is stored encrypted in the user model."""
    settings.OIDC_OP_TOKEN_ENDPOINT = "https://oidc.example.com/token"
    settings.OIDC_RP_CLIENT_ID = "osmose-client"
    settings.OIDC_RP_CLIENT_SECRET = "osmose-secret"

    user = _make_user(
        access_token="expired-tok",
        refresh_token="old-refresh",
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    with patch("core.destinations.drive.drive_backend.requests") as mock_requests:
        mock_requests.post.return_value.json.return_value = {
            "access_token": "new-access",
            "refresh_token": "new-plaintext-refresh",
            "expires_in": 60,
        }
        mock_requests.post.return_value.raise_for_status = MagicMock()
        DriveUserTokenBackend(user)._get_token()

    assert user.oidc_refresh_token != "new-plaintext-refresh"
    assert decrypt_token(user.oidc_refresh_token) == "new-plaintext-refresh"


# ---------------------------------------------------------------------------
# user_has_usable_drive_token()
# ---------------------------------------------------------------------------


def test_has_usable_token_returns_false_when_no_tokens():
    """Returns False when both access and refresh tokens are empty."""
    user = _make_user(access_token="", refresh_token="", expires_at=None)
    assert user_has_usable_drive_token(user) is False


def test_has_usable_token_returns_true_when_valid_access_token():
    """Returns True when the access token is present and not yet expired."""
    user = _make_user(
        access_token="valid-tok",
        refresh_token="",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    assert user_has_usable_drive_token(user) is True


def test_has_usable_token_returns_false_when_access_expired_and_no_refresh():
    """Returns False when the access token is expired and no refresh token is stored."""
    user = _make_user(
        access_token="expired-tok",
        refresh_token="",
        expires_at=timezone.now() - timedelta(seconds=1),
    )
    assert user_has_usable_drive_token(user) is False


def test_has_usable_token_returns_true_when_access_expired_but_refresh_present():
    """Returns True when the access token is expired but a refresh token exists."""
    user = _make_user(
        access_token="expired-tok",
        refresh_token="valid-refresh",
        expires_at=timezone.now() - timedelta(seconds=1),
    )
    assert user_has_usable_drive_token(user) is True


def test_has_usable_token_returns_true_when_no_expiry_date():
    """Returns True when access token is present but expires_at is None (trust it)."""
    user = _make_user(access_token="tok", refresh_token="", expires_at=None)
    assert user_has_usable_drive_token(user) is True
