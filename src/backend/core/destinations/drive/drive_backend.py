"""DriveBackend — HTTP client for La Suite Drive API."""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

import requests


class DriveBackend:
    """Base HTTP client for La Suite Drive API.

    Subclasses implement _refresh() and _api_prefix() to select the auth strategy
    and the API family (/external_api/v1.0 vs /api/v1.0).
    """

    def __init__(self):
        self._access_token = None
        self._token_expires_at = None

    def _get_token(self) -> str:
        """Return a valid access token, refreshing proactively if near expiry."""
        buffer = timedelta(seconds=10)
        if (
            self._access_token is None
            or self._token_expires_at is None
            or timezone.now() >= self._token_expires_at - buffer
        ):
            self._refresh()
        return self._access_token

    def _refresh(self):
        raise NotImplementedError

    def _api_prefix(self) -> str:
        raise NotImplementedError

    def _base_url(self) -> str:
        return settings.DRIVE_API_BASE_URL

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._get_token()}"}

    # --- Folder operations ---

    def create_folder(self, title: str) -> dict:
        """Create a root folder in Drive. Returns the item dict (includes 'id')."""
        response = requests.post(
            f"{self._base_url()}{self._api_prefix()}/items/",
            json={"type": "folder", "title": title},
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def create_subfolder(self, title: str, parent_id: str) -> dict:
        """Create a child folder inside an existing Drive folder."""
        response = requests.post(
            f"{self._base_url()}{self._api_prefix()}/items/{parent_id}/children/",
            json={"type": "folder", "title": title},
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    # --- File upload (3-step) ---

    def create_file_item(self, filename: str, parent_id: str) -> dict:
        """Step 1: Create a file item. Returns item dict including S3 presigned URL in 'policy'."""
        response = requests.post(
            f"{self._base_url()}{self._api_prefix()}/items/{parent_id}/children/",
            json={"type": "file", "filename": filename},
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def upload_to_s3(self, policy_url: str, file_path: str) -> None:
        """Step 2: Upload file content directly to the S3 presigned URL (no Drive token)."""
        with open(file_path, "rb") as f:
            response = requests.put(
                policy_url, data=f.read(), headers={"x-amz-acl": "private"}, timeout=300
            )
        response.raise_for_status()

    def notify_upload_ended(self, item_id: str) -> None:
        """Step 3: Notify Drive that the S3 upload is complete."""
        response = requests.post(
            f"{self._base_url()}{self._api_prefix()}/items/{item_id}/upload-ended/",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()

    # --- Sharing ---

    def find_user_by_email(self, email: str) -> dict | None:
        """Resolve an email to a Drive user dict. Returns None if not found."""
        response = requests.get(
            f"{self._base_url()}/api/v1.0/users/",
            params={"q": email},
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        results = data if isinstance(data, list) else data.get("results", [])
        return results[0] if results else None

    def share_with_user(self, item_id: str, user_id: str) -> None:
        """Grant owner access to an existing Drive user."""
        response = requests.post(
            f"{self._base_url()}{self._api_prefix()}/items/{item_id}/accesses/",
            json={"user_id": user_id, "role": "owner"},
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()

    def invite_by_email(self, item_id: str, email: str) -> None:
        """Invite a user not yet registered in Drive as owner."""
        response = requests.post(
            f"{self._base_url()}{self._api_prefix()}/items/{item_id}/invitations/",
            json={"email": email, "role": "owner"},
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()


class DriveServiceAccountBackend(DriveBackend):
    """Uses OAuth2 client_credentials grant. Targets /external_api/v1.0/."""

    def _api_prefix(self) -> str:
        return "/external_api/v1.0"

    def _refresh(self):
        response = requests.post(
            settings.DRIVE_OIDC_TOKEN_ENDPOINT,
            data={
                "grant_type": "client_credentials",
                "client_id": settings.DRIVE_OIDC_CLIENT_ID,
                "client_secret": settings.DRIVE_OIDC_CLIENT_SECRET,
                "scope": "openid email",
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        self._access_token = data["access_token"]
        expires_in = data.get("expires_in")
        self._token_expires_at = (
            timezone.now() + timedelta(seconds=expires_in) if expires_in else None
        )


class DriveUserTokenBackend(DriveBackend):
    """Uses the authenticated user's ProConnect token. Targets /api/v1.0/.

    The token is seeded from user.oidc_access_token and refreshed via
    user.oidc_refresh_token when it expires. Updated tokens are persisted
    back to the user model so subsequent Celery tasks can reuse them.
    """

    def __init__(self, user):
        super().__init__()
        self._user = user
        self._access_token = user.oidc_access_token or None
        self._token_expires_at = user.oidc_token_expires_at

    def _api_prefix(self) -> str:
        return "/api/v1.0"

    def _refresh(self):
        if not self._user.oidc_refresh_token:
            raise RuntimeError(
                f"No refresh token stored for user {self._user.email}. "
                "Cannot refresh the ProConnect token for Drive migration."
            )
        response = requests.post(
            settings.OIDC_OP_TOKEN_ENDPOINT,
            data={
                "grant_type": "refresh_token",
                "client_id": settings.OIDC_RP_CLIENT_ID,
                "client_secret": settings.OIDC_RP_CLIENT_SECRET,
                "refresh_token": self._user.oidc_refresh_token,
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        expires_in = data.get("expires_in")
        self._access_token = data["access_token"]
        self._token_expires_at = (
            timezone.now() + timedelta(seconds=expires_in) if expires_in else None
        )
        self._user.oidc_access_token = self._access_token
        if data.get("refresh_token"):
            self._user.oidc_refresh_token = data["refresh_token"]
        self._user.oidc_token_expires_at = self._token_expires_at
        self._user.save(
            update_fields=[
                "oidc_access_token",
                "oidc_refresh_token",
                "oidc_token_expires_at",
                "updated_at",
            ]
        )
