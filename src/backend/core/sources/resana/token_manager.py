"""Resana token lifecycle management for per-user authentication."""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

import requests

from core.encryption import decrypt_token, encrypt_token

TOKEN_LIFETIME_HOURS = 3
REFRESH_BUFFER_SECONDS = 60


class ResanaTokenExpired(Exception):
    """Raised when the Resana access token is expired and no refresh token is available."""


class ResanaTokenManager:
    def __init__(self, user):
        self.user = user

    def is_connected(self) -> bool:
        """Return True if an access token is stored (regardless of validity)."""
        return bool(self.user.resana_access_token)

    def clear_tokens(self) -> None:
        """Remove all stored Resana tokens, marking the user as disconnected."""
        self.user.resana_access_token = ""
        self.user.resana_refresh_token = ""
        self.user.resana_token_expires_at = None
        self.user.save()

    def store_tokens(self, access: str, refresh: str) -> None:
        """Encrypt and persist both tokens. Sets expires_at = now + 3h."""
        self.user.resana_access_token = encrypt_token(access)
        self.user.resana_refresh_token = encrypt_token(refresh)
        self.user.resana_token_expires_at = timezone.now() + timedelta(
            hours=TOKEN_LIFETIME_HOURS
        )
        self.user.save()

    def get_valid_token(self) -> str:
        """Return a valid decrypted access token, refreshing proactively if near expiry.

        Raises ResanaTokenExpired if expired and no refresh token is stored.
        """
        expires_at = self.user.resana_token_expires_at
        buffer = timedelta(seconds=REFRESH_BUFFER_SECONDS)
        token_is_fresh = expires_at is not None and timezone.now() < expires_at - buffer

        if not token_is_fresh:
            if not self.user.resana_refresh_token:
                raise ResanaTokenExpired(
                    "Resana access token expired and no refresh token is stored."
                )
            self._refresh()

        return decrypt_token(self.user.resana_access_token)

    def _refresh(self) -> None:
        """Obtain a new access token using the stored refresh token."""
        refresh_token = decrypt_token(self.user.resana_refresh_token)
        response = requests.post(
            settings.RESANA_AUTHSERVICE_ENDPOINT + "/public/token/access",
            headers={
                "Cookie": f"interstis_refresh={refresh_token}",
                "Accept": "application/json",
            },
            timeout=30,
        )
        if response.status_code == 401:
            self.clear_tokens()
            raise ResanaTokenExpired("Resana refresh token is no longer valid.")
        response.raise_for_status()
        new_access = response.json()["access_token"]
        self.user.resana_access_token = encrypt_token(new_access)
        self.user.resana_token_expires_at = timezone.now() + timedelta(
            hours=TOKEN_LIFETIME_HOURS
        )
        self.user.save()
