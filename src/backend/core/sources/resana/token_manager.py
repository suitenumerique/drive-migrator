"""Resana token lifecycle management for per-user authentication."""

import datetime
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

import jwt
import requests

from core.encryption import decrypt_token, encrypt_token

REFRESH_BUFFER_SECONDS = 60


class ResanaTokenExpired(Exception):
    """Raised when the Resana access token is expired and no refresh token is available."""


def _token_expires_at(access_token: str) -> datetime.datetime:
    """Return the access token's own expiry, read from its `exp` JWT claim.

    Signature isn't verified: this is only used to schedule our own proactive
    refresh, not as a trust decision, and the token just came straight from
    Resana over TLS.
    """
    claims = jwt.decode(access_token, options={"verify_signature": False})
    return datetime.datetime.fromtimestamp(claims["exp"], tz=datetime.timezone.utc)


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
        """Encrypt and persist both tokens. expires_at comes from the access token itself."""
        self.user.resana_access_token = encrypt_token(access)
        self.user.resana_refresh_token = encrypt_token(refresh)
        self.user.resana_token_expires_at = _token_expires_at(access)
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

        new_access = response.cookies.get("interstis_access")
        if not new_access:
            self.clear_tokens()
            raise ResanaTokenExpired(
                "Resana refresh response did not set an interstis_access cookie."
            )

        self.user.resana_access_token = encrypt_token(new_access)
        self.user.resana_token_expires_at = _token_expires_at(new_access)
        self.user.save()
