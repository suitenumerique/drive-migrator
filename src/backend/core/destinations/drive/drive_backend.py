"""DriveBackend — HTTP client for La Suite Drive API."""

from django.conf import settings

import requests


class DriveBackend:
    """
    Low-level HTTP client for La Suite Drive external API.

    Authentication uses the OAuth2 client_credentials flow (OIDC Resource Server).
    All operations use a bearer token obtained via get_access_token().
    """

    def get_access_token(self) -> str:
        """Obtain a bearer token via OAuth2 client_credentials grant."""
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
        return response.json()["access_token"]

    def _headers(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def _base_url(self) -> str:
        return settings.DRIVE_API_BASE_URL

    # --- Folder operations ---

    def create_folder(self, title: str, token: str) -> dict:
        """Create a root folder in Drive. Returns the item dict (includes 'id')."""
        response = requests.post(
            f"{self._base_url()}/external_api/v1.0/items/",
            json={"type": "folder", "title": title},
            headers=self._headers(token),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def create_subfolder(self, title: str, parent_id: str, token: str) -> dict:
        """Create a child folder inside an existing Drive folder."""
        response = requests.post(
            f"{self._base_url()}/external_api/v1.0/items/{parent_id}/children/",
            json={"type": "folder", "title": title},
            headers=self._headers(token),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    # --- File upload (3-step) ---

    def create_file_item(self, filename: str, parent_id: str, token: str) -> dict:
        """
        Step 1: Create a file item in Drive.
        Returns the item dict including the S3 presigned URL in 'policy'.
        """
        response = requests.post(
            f"{self._base_url()}/external_api/v1.0/items/{parent_id}/children/",
            json={"type": "file", "filename": filename},
            headers=self._headers(token),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def upload_to_s3(self, policy_url: str, file_path: str) -> None:
        """Step 2: Upload the file content directly to the S3 presigned URL."""
        with open(file_path, "rb") as f:
            response = requests.put(
                policy_url, data=f.read(), headers={"x-amz-acl": "private"}, timeout=300
            )
        response.raise_for_status()

    def notify_upload_ended(self, item_id: str, token: str) -> None:
        """Step 3: Notify Drive that the S3 upload is complete."""
        response = requests.post(
            f"{self._base_url()}/external_api/v1.0/items/{item_id}/upload-ended/",
            headers=self._headers(token),
            timeout=30,
        )
        response.raise_for_status()

    # --- Sharing ---

    def find_user_by_email(self, email: str, token: str) -> dict | None:
        """
        Resolve an email address to a Drive user dict.
        Returns None if the user does not exist in Drive.
        """
        response = requests.get(
            f"{self._base_url()}/api/v1.0/users/",
            params={"q": email},
            headers=self._headers(token),
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        results = data if isinstance(data, list) else data.get("results", [])
        return results[0] if results else None

    def share_with_user(self, item_id: str, user_id: str, token: str) -> None:
        """Grant owner access to an existing Drive user (item appears in their account)."""
        response = requests.post(
            f"{self._base_url()}/external_api/v1.0/items/{item_id}/accesses/",
            json={"user_id": user_id, "role": "owner"},
            headers=self._headers(token),
            timeout=30,
        )
        response.raise_for_status()

    def invite_by_email(self, item_id: str, email: str, token: str) -> None:
        """Invite a user not yet registered in Drive as owner."""
        response = requests.post(
            f"{self._base_url()}/external_api/v1.0/items/{item_id}/invitations/",
            json={"email": email, "role": "owner"},
            headers=self._headers(token),
            timeout=30,
        )
        response.raise_for_status()
