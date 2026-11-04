"""Resana token lifecycle management for per-user authentication."""

import datetime
import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

import jwt
import requests
from cryptography.fernet import InvalidToken

from core.backends.source import SourceTokenExpired
from core.encryption import decrypt_token, encrypt_token
from core.sources.resana.migrator_auth import (
    MigratorClient,
    bridge_session_url,
    create_bridge_session,
    refresh_offline_token,
)
from core.utils import (
    describe_access_expiry,
    describe_expiry,
    describe_oauth_error,
    describe_request_error,
)

logger = logging.getLogger(__name__)

REFRESH_BUFFER_SECONDS = 60
# Without an offline token nothing can renew the access token: stop the run
# early enough for its cleanup to unlock the workspace with what is left.
NO_OFFLINE_TOKEN_BUFFER_SECONDS = 10 * 60
# Keycloak ends an offline session left unused for its idle timeout (1 h for the
# DINUM client), long before the access token (3 h) needs a refresh. Wide, so a
# single long download can't step over the whole refresh window.
OFFLINE_REFRESH_BUFFER_SECONDS = 30 * 60
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
RESANA_TOKEN_CLEAR_FIELDS = RESANA_TOKEN_FIELDS + ("updated_at",)
OFFLINE_EXPIRY_ERRORS = (
    jwt.PyJWTError,
    InvalidToken,
    TypeError,
    ValueError,
    OverflowError,
    OSError,
)


class ResanaTokenExpired(SourceTokenExpired):
    """Raised when the Resana access token is expired and no refresh token is available."""


def _token_expires_at(access_token: str) -> datetime.datetime:
    """Return the access token's own expiry, read from its `exp` JWT claim.

    Signature isn't verified: this is only used to schedule our own proactive
    refresh, not as a trust decision, and the token just came straight from
    Resana over TLS.
    """
    claims = jwt.decode(access_token, options={"verify_signature": False})
    return datetime.datetime.fromtimestamp(claims["exp"], tz=datetime.timezone.utc)


def _claim_time(claims: dict, name: str) -> datetime.datetime | None:
    value = claims.get(name)
    if not value:
        return None
    return datetime.datetime.fromtimestamp(value, tz=datetime.timezone.utc)


def _is_server_error(error: Exception) -> bool:
    response = getattr(error, "response", None)
    return response is not None and response.status_code >= 500


def migrator_client_from_settings() -> MigratorClient:
    return MigratorClient(
        client_id=settings.RESANA_MIGRATOR_CLIENT_ID,
        client_secret=settings.RESANA_MIGRATOR_CLIENT_SECRET,
        redirect_uri=settings.RESANA_MIGRATOR_REDIRECT_URI,
    )


class ResanaTokenManager:
    def __init__(self, user):
        self.user = user

    def describe_tokens(self) -> str:
        """Describe the stored tokens and their lifetime for logs, never their value."""
        access = "access token missing"
        if self.user.resana_access_token:
            access = f"access token present, {describe_access_expiry(self.user.resana_token_expires_at)}"
        offline = "offline token missing"
        if self.user.resana_refresh_token:
            offline = f"offline token present, {self._describe_offline_expiry()}"
        return f"Resana tokens for user {self.user.id}: {access}; {offline}"

    def _offline_expires_at(self) -> datetime.datetime | None:
        """Keycloak decides the offline token's lifetime server side: its exp
        claim, when the realm caps the offline session, is the idle deadline.

        Raises one of OFFLINE_EXPIRY_ERRORS when the token can't be read.
        """
        return _claim_time(self._offline_claims(), "exp")

    def _offline_claims(self) -> dict:
        return jwt.decode(
            decrypt_token(self.user.resana_refresh_token),
            options={"verify_signature": False},
        )

    def _describe_offline_expiry(self) -> str:
        try:
            return describe_expiry(self._offline_expires_at())
        except OFFLINE_EXPIRY_ERRORS:
            return "expiry unreadable"

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
        self.user.save(update_fields=RESANA_TOKEN_CLEAR_FIELDS)

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

    def ensure_offline_token(self) -> None:
        """Before a long task locks anything: refresh now an offline token close
        to its idle deadline, and raise ResanaTokenExpired if it is missing or
        refused, rather than hours later. An offline token without a readable
        exp claim is trusted."""
        self.get_valid_token(strict=True)

    def get_valid_token(self, strict: bool = False) -> str:
        """Return a valid decrypted access token, refreshing proactively if near expiry.

        Raises ResanaTokenExpired if the token cannot be renewed or decrypted.
        The stored tokens are then cleared, except an access token that still
        works: get_token_for_cleanup can still use it to unlock the workspace.

        Refreshes are serialized per user: concurrent workers (e.g. several
        workspace exports) would otherwise each submit the same offline token,
        and the loser would clear the tokens the winner just stored.
        """
        try:
            return self._get_valid_token(strict)
        except InvalidToken as exc:
            # Encrypted with another key: the user must log in again.
            self.clear_tokens()
            raise ResanaTokenExpired(
                "Stored Resana tokens cannot be decrypted."
            ) from exc

    def _get_valid_token(self, strict: bool) -> str:
        if (
            self._token_is_fresh()
            and self._offline_token_is_fresh()
            and (self.user.resana_refresh_token or not strict)
        ):
            return decrypt_token(self.user.resana_access_token)

        with cache.lock(
            f"resana:refresh:{self.user.pk}", timeout=REFRESH_LOCK_TIMEOUT_SECONDS
        ):
            # Another worker may have refreshed while we waited for the lock.
            self.user.refresh_from_db(fields=RESANA_TOKEN_FIELDS)
            if strict and not self.user.resana_refresh_token:
                self.clear_tokens()
                raise ResanaTokenExpired("No Resana offline token is stored.")
            if not self._token_is_fresh():
                # In its last minute the access token still works: keep it for
                # the cleanup that follows the run this failure stops.
                keep_access_token = not strict and self._access_token_works()
                if not self.user.resana_refresh_token:
                    if not keep_access_token:
                        self.clear_tokens()
                    raise ResanaTokenExpired(
                        "Resana access token expired and no refresh token is stored."
                    )
                self._refresh(keep_access_token=keep_access_token)
            elif not self._offline_token_is_fresh():
                if strict:
                    self._refresh()
                else:
                    self._refresh_ahead()

        return decrypt_token(self.user.resana_access_token)

    def get_token_for_cleanup(self) -> str:
        """After a failed run: a refreshed access token if possible, else the
        last minute of the stored one, so the workspace can still be unlocked."""
        try:
            return self.get_valid_token()
        except (ResanaTokenExpired, requests.RequestException):
            self.user.refresh_from_db(fields=RESANA_TOKEN_FIELDS)
            if self.user.resana_access_token and self._access_token_works():
                return decrypt_token(self.user.resana_access_token)
            raise

    def _access_token_works(self) -> bool:
        expires_at = self.user.resana_token_expires_at
        return expires_at is not None and timezone.now() < expires_at

    def _token_is_fresh(self) -> bool:
        expires_at = self.user.resana_token_expires_at
        buffer = timedelta(
            seconds=REFRESH_BUFFER_SECONDS
            if self.user.resana_refresh_token
            else NO_OFFLINE_TOKEN_BUFFER_SECONDS
        )
        return expires_at is not None and timezone.now() < expires_at - buffer

    def _offline_token_is_fresh(self) -> bool:
        """Without a readable deadline, only the access token expiry matters."""
        if not self.user.resana_refresh_token:
            return True
        try:
            claims = self._offline_claims()
            expires_at = _claim_time(claims, "exp")
            issued_at = _claim_time(claims, "iat")
        except OFFLINE_EXPIRY_ERRORS:
            return True
        if expires_at is None:
            return True
        buffer = timedelta(seconds=OFFLINE_REFRESH_BUFFER_SECONDS)
        if issued_at is not None:
            # Near the session max lifespan, Keycloak issues tokens living less
            # than the margin: refresh them at half life, not on every call.
            buffer = min(buffer, max((expires_at - issued_at) / 2, timedelta(0)))
        return timezone.now() < expires_at - buffer

    def _refresh_ahead(self) -> None:
        """Refresh before the offline token dies of inactivity. The access token
        is still valid: a failure must not end the run before it expires."""
        try:
            self._refresh(keep_access_token=True)
        except (ResanaTokenExpired, requests.RequestException):
            logger.warning(
                "Resana token refresh ahead failed for user %s: keeping the "
                "access token, which %s",
                self.user.id,
                describe_access_expiry(self.user.resana_token_expires_at),
            )

    def _expire(self, message: str, keep_access_token: bool) -> ResanaTokenExpired:
        """Drop what the refused refresh made useless and return the error to raise.

        With keep_access_token, only the offline token goes: the access token
        keeps working until it expires, and the refused offline token is not
        submitted again.
        """
        if keep_access_token:
            self.user.resana_refresh_token = ""
            self.user.save(update_fields=("resana_refresh_token", "updated_at"))
        else:
            self.clear_tokens()
        return ResanaTokenExpired(message)

    def _refresh(self, keep_access_token: bool = False) -> None:
        """Renew the stored access token and Resana session from the offline token.

        Raises ResanaTokenExpired if the offline token is no longer accepted.
        The stored tokens are then cleared, except the access token when
        keep_access_token is set: it keeps working until it expires.
        """
        logger.info(
            "Resana token refresh for user %s: access token %s, offline token %s, "
            "refreshing with the offline token",
            self.user.id,
            describe_access_expiry(self.user.resana_token_expires_at),
            self._describe_offline_expiry(),
        )
        offline_token = decrypt_token(self.user.resana_refresh_token)
        try:
            token_response = refresh_offline_token(
                settings.RESANA_KEYCLOAK_ENDPOINT,
                migrator_client_from_settings(),
                offline_token,
            )
        except requests.HTTPError as exc:
            self._log_refresh_failure(f"Keycloak returned {describe_oauth_error(exc)}")
            if exc.response is not None and exc.response.status_code in (400, 401):
                raise self._expire(
                    "Resana offline token is no longer valid.", keep_access_token
                ) from exc
            raise
        except requests.RequestException as exc:
            self._log_refresh_failure(
                f"Keycloak request failed ({describe_request_error(exc)})"
            )
            raise

        new_access = token_response.get("access_token")
        new_offline = token_response.get("refresh_token") or offline_token
        if not new_access:
            self._log_refresh_failure("Keycloak returned no access token")
            raise self._expire(
                "Keycloak refresh response did not return an access_token.",
                keep_access_token,
            )

        try:
            session = create_bridge_session(
                bridge_session_url(settings.RESANA_WEB_ENDPOINT),
                new_access,
                existing_session_id=self.get_session_id() or None,
            )
        except (requests.HTTPError, ValueError) as exc:
            if _is_server_error(exc):
                # Transient: keep the tokens for a later retry.
                self._log_refresh_failure(
                    f"the Resana bridge failed ({describe_request_error(exc)})"
                )
                raise
            self._log_refresh_failure(
                "the Resana bridge rejected the new session "
                f"({describe_request_error(exc)})"
            )
            raise self._expire(
                "Resana bridge rejected the refreshed session.", keep_access_token
            ) from exc
        except requests.RequestException as exc:
            self._log_refresh_failure(
                f"the Resana bridge request failed ({describe_request_error(exc)})"
            )
            raise

        self.store_tokens(
            offline_token=new_offline,
            access_token=session["interstis_access"],
            session_id=session["plateformeSessionId"],
            csrf_token=session["csrfToken"],
        )
        logger.info(
            "Resana token refresh succeeded for user %s: new access token %s, "
            "offline token %s, %s",
            self.user.id,
            describe_access_expiry(self.user.resana_token_expires_at),
            "rotated" if new_offline != offline_token else "unchanged",
            self._describe_offline_expiry(),
        )

    def _log_refresh_failure(self, reason: str) -> None:
        logger.warning(
            "Resana token refresh failed for user %s: %s", self.user.id, reason
        )
