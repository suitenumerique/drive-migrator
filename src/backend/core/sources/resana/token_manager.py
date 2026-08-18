"""Resana token lifecycle management for per-user authentication."""

import datetime
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

import jwt
import requests

from core.encryption import decrypt_token, encrypt_token
from core.sources.resana.migrator_auth import (
    MigratorClient,
    bridge_session_url,
    create_bridge_session,
    refresh_offline_token,
)

REFRESH_BUFFER_SECONDS = 60
# Upper bound on how long one worker may hold the per-user refresh lock: covers
# the two upstream calls (Keycloak + bridge, 30s timeout each) with margin.
REFRESH_LOCK_TIMEOUT_SECONDS = 90
RESANA_TOKEN_FIELDS = (
    "resana_access_token",
    "resana_refresh_token",
    "resana_session_id",
    "resana_csrf_token",
    "resana_token_expires_at",
)


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


def migrator_client_from_settings() -> MigratorClient:
    return MigratorClient(
        client_id=settings.RESANA_MIGRATOR_CLIENT_ID,
        client_secret=settings.RESANA_MIGRATOR_CLIENT_SECRET,
        redirect_uri=settings.RESANA_MIGRATOR_REDIRECT_URI,
    )


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
        self.user.resana_session_id = ""
        self.user.resana_csrf_token = ""
        self.user.resana_token_expires_at = None
        self.user.save(update_fields=RESANA_TOKEN_FIELDS)

    def store_tokens(
        self,
        *,
        offline_token: str,
        access_token: str,
        session_id: str,
        csrf_token: str,
    ) -> None:
        """Persist a Keycloak offline token and the bridge session it unlocked.

        expires_at is read from the bridge's interstis_access token itself.
        """
        self.user.resana_refresh_token = encrypt_token(offline_token)
        self.user.resana_access_token = encrypt_token(access_token)
        self.user.resana_session_id = encrypt_token(session_id)
        self.user.resana_csrf_token = encrypt_token(csrf_token)
        self.user.resana_token_expires_at = _token_expires_at(access_token)
        self.user.save(update_fields=RESANA_TOKEN_FIELDS)

    def get_session_id(self) -> str:
        """Return the decrypted PHPSESSID for the legacy portal routes."""
        return decrypt_token(self.user.resana_session_id)

    def get_csrf_token(self) -> str:
        """Return the decrypted csrfToken for the legacy portal's X-CSRF-TOKEN header."""
        return decrypt_token(self.user.resana_csrf_token)

    def get_valid_token(self) -> str:
        """Return a valid decrypted access token, refreshing proactively if near expiry.

        Raises ResanaTokenExpired if expired and no refresh token is stored.

        Refreshes are serialized per user: concurrent workers (e.g. several
        workspace exports) would otherwise each submit the same offline token,
        and the loser would clear the tokens the winner just stored.
        """
        if self._token_is_fresh():
            return decrypt_token(self.user.resana_access_token)

        with cache.lock(
            f"resana:refresh:{self.user.pk}", timeout=REFRESH_LOCK_TIMEOUT_SECONDS
        ):
            # Another worker may have refreshed while we waited for the lock.
            self.user.refresh_from_db(fields=RESANA_TOKEN_FIELDS)
            if not self._token_is_fresh():
                if not self.user.resana_refresh_token:
                    raise ResanaTokenExpired(
                        "Resana access token expired and no refresh token is stored."
                    )
                self._refresh()

        return decrypt_token(self.user.resana_access_token)

    def _token_is_fresh(self) -> bool:
        expires_at = self.user.resana_token_expires_at
        buffer = timedelta(seconds=REFRESH_BUFFER_SECONDS)
        return expires_at is not None and timezone.now() < expires_at - buffer

    def _refresh(self) -> None:
        """Renew the stored access token and Resana session from the offline token.

        Raises ResanaTokenExpired (and clears the stored tokens) if the
        offline token is no longer accepted.
        """
        offline_token = decrypt_token(self.user.resana_refresh_token)
        try:
            token_response = refresh_offline_token(
                settings.RESANA_KEYCLOAK_ENDPOINT,
                migrator_client_from_settings(),
                offline_token,
            )
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (400, 401):
                self.clear_tokens()
                raise ResanaTokenExpired(
                    "Resana offline token is no longer valid."
                ) from exc
            raise

        new_access = token_response.get("access_token")
        new_offline = token_response.get("refresh_token") or offline_token
        if not new_access:
            self.clear_tokens()
            raise ResanaTokenExpired(
                "Keycloak refresh response did not return an access_token."
            )

        try:
            session = create_bridge_session(
                bridge_session_url(settings.RESANA_WEB_ENDPOINT),
                new_access,
                existing_session_id=self.get_session_id() or None,
            )
        except (requests.HTTPError, ValueError) as exc:
            self.clear_tokens()
            raise ResanaTokenExpired(
                "Resana bridge rejected the refreshed session."
            ) from exc

        self.store_tokens(
            offline_token=new_offline,
            access_token=session["interstis_access"],
            session_id=session["plateformeSessionId"],
            csrf_token=session["csrfToken"],
        )
