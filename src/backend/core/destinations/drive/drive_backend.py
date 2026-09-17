"""DriveBackend — HTTP client for La Suite Drive API."""

import logging
import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

import requests
from celery.utils.log import get_task_logger
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import HTTPError, Timeout
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    retry_if_exception_type,
    wait_exponential,
)

from core.encryption import decrypt_token, encrypt_token
from core.retry_utils import log_final_failure_and_reraise

logger = get_task_logger(__name__)

_UPLOAD_STATE_NOT_PENDING = "item_upload_state_not_pending"
_EXISTING_ID = "item_create_existing_id"


def _stop_after_configured_attempts(retry_state) -> bool:
    """Read DRIVE_RETRY_MAX_ATTEMPTS at call time, not decoration time, so it
    stays overridable per-test/per-environment like every other setting here."""
    return retry_state.attempt_number >= settings.DRIVE_RETRY_MAX_ATTEMPTS


def _wait_configured_backoff(retry_state) -> float:
    """Same rationale as _stop_after_configured_attempts: read settings live."""
    return wait_exponential(
        multiplier=settings.DRIVE_RETRY_WAIT_MULTIPLIER,
        min=settings.DRIVE_RETRY_WAIT_MIN,
    )(retry_state)


def _is_server_error(error: BaseException) -> bool:
    """5xx may mean Drive processed the request without rolling back (item
    creation isn't wrapped in a transaction), so callers must check before
    retrying. 4xx is permanent and must fail fast."""
    return (
        isinstance(error, HTTPError)
        and error.response is not None
        and error.response.status_code >= 500
    )


# Retries on a transient network error or a Drive-side 5xx.
_retry_on_transient_error = retry(
    retry=retry_if_exception_type((Timeout, RequestsConnectionError))
    | retry_if_exception(_is_server_error),
    stop=_stop_after_configured_attempts,
    wait=_wait_configured_backoff,
    before_sleep=before_sleep_log(logger, logging.INFO),
    retry_error_callback=log_final_failure_and_reraise(logger),
)


def _has_error_code(error: HTTPError, status_code: int, code: str) -> bool:
    """Check whether error's response is a standardized-error payload carrying
    the given error code (drf-standardized-errors' {"errors": [{"code": ...}]})."""
    response = error.response
    if response is None or response.status_code != status_code:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    return any(
        error_detail.get("code") == code for error_detail in payload.get("errors", [])
    )


def _is_upload_already_processed(error: HTTPError) -> bool:
    """A retried ReadTimeout can hit this 400 if the original call actually
    already succeeded."""
    return _has_error_code(error, 400, _UPLOAD_STATE_NOT_PENDING)


def _is_duplicate_id_conflict(error: HTTPError) -> bool:
    """Drive rejects reusing an id an earlier, ambiguously-failed attempt already
    committed with."""
    return _has_error_code(error, 400, _EXISTING_ID)


def clear_drive_tokens(user) -> None:
    """Remove the stored Drive (ProConnect) tokens, forcing the user to log in again."""
    user.oidc_access_token = ""
    user.oidc_refresh_token = ""
    user.oidc_token_expires_at = None
    user.save(
        update_fields=[
            "oidc_access_token",
            "oidc_refresh_token",
            "oidc_token_expires_at",
            "updated_at",
        ]
    )


def user_has_usable_drive_token(user) -> bool:
    """Return True if user has a Drive token that can be used or refreshed."""
    has_access = bool(user.oidc_access_token)
    has_refresh = bool(user.oidc_refresh_token)

    if not has_access:
        return False

    expires_at = user.oidc_token_expires_at
    buffer = timedelta(seconds=10)
    access_is_valid = expires_at is None or timezone.now() < expires_at - buffer

    if access_is_valid:
        return True

    return has_refresh


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
        url = f"{self._base_url()}{self._api_prefix()}/items/"
        payload = {"type": "folder", "title": title}
        return self._create_item(url, payload)

    def create_subfolder(self, title: str, parent_id: str) -> dict:
        """Create a child folder inside an existing Drive folder."""
        url = f"{self._base_url()}{self._api_prefix()}/items/{parent_id}/children/"
        payload = {"type": "folder", "title": title}
        return self._create_item(url, payload)

    # --- File upload (3-step) ---

    def create_file_item(self, filename: str, parent_id: str) -> dict:
        """Step 1: Create a file item. Returns item dict including S3 presigned URL in 'policy'."""
        url = f"{self._base_url()}{self._api_prefix()}/items/{parent_id}/children/"
        payload = {"type": "file", "filename": filename}
        item = self._create_item(url, payload)
        if "policy" not in item:
            item = self._replace_recovered_pending_file(item, url, payload)
        return item

    def _replace_recovered_pending_file(
        self, existing_item: dict, url: str, payload: dict
    ) -> dict:
        """A recovered file item has no upload policy (only the create response
        carries one, and Drive can't regenerate it). Still pending, so delete the
        orphan and create a fresh item instead of getting stuck."""
        if existing_item.get("upload_state") != "pending":
            raise RuntimeError(
                f"Recovered Drive item {existing_item.get('id')} is not pending "
                f"upload (upload_state={existing_item.get('upload_state')!r}); "
                "can't safely replace it with a fresh create."
            )
        self._delete_item(existing_item["id"])
        return self._create_item(url, payload)

    @_retry_on_transient_error
    def _delete_item(self, item_id: str) -> None:
        """DELETE /items/{id}/ (soft delete)."""
        url = f"{self._base_url()}{self._api_prefix()}/items/{item_id}/"
        response = requests.delete(url, headers=self._headers(), timeout=30)
        response.raise_for_status()

    def _create_item(self, url: str, payload: dict) -> dict:
        """POST with a client-generated id, retried via tenacity on 5xx/network
        errors or a Drive id conflict: creation isn't idempotent (Drive silently
        renames title duplicates), so on a retryable failure we check for the item
        by id before giving up, instead of duplicating it or failing a write that
        actually succeeded. See #208."""
        item_id = str(uuid.uuid4())
        payload = {**payload, "id": item_id}

        @_retry_on_transient_error
        def do_create_item():
            try:
                response = requests.post(
                    url, json=payload, headers=self._headers(), timeout=30
                )
                response.raise_for_status()
                return response.json()
            except HTTPError as http_error:
                if not (
                    _is_server_error(http_error)
                    or _is_duplicate_id_conflict(http_error)
                ):
                    logger.warning(
                        "item creation failed (%s) for %s: %s",
                        http_error.response.status_code,
                        url,
                        http_error.response.text[:2000],
                    )
                    raise
                existing_item = self._get_item_if_exists_or_none(item_id)
                if existing_item is not None:
                    return existing_item
                raise
            except (Timeout, RequestsConnectionError):
                existing_item = self._get_item_if_exists_or_none(item_id)
                if existing_item is not None:
                    return existing_item
                raise

        return do_create_item()

    def _get_item_if_exists(self, item_id: str) -> dict | None:
        """GET /items/{id}/, treating a 404 as 'not created yet' rather than an error."""
        url = f"{self._base_url()}{self._api_prefix()}/items/{item_id}/"
        response = requests.get(url, headers=self._headers(), timeout=30)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def _get_item_if_exists_or_none(self, item_id: str) -> dict | None:
        """Like _get_item_if_exists(), but only swallows a transient failure (5xx,
        network) of the check itself, so the caller retries the POST. A permanent
        failure (401/403) is re-raised instead of masked."""
        try:
            return self._get_item_if_exists(item_id)
        except HTTPError as http_error:
            if not _is_server_error(http_error):
                raise
            logger.info(
                "existence check for item %s failed (%s), will retry",
                item_id,
                http_error,
            )
            return None
        except (Timeout, RequestsConnectionError) as network_error:
            logger.info(
                "existence check for item %s failed (%s), will retry",
                item_id,
                network_error,
            )
            return None

    @_retry_on_transient_error
    def upload_to_s3(self, policy_url: str, file_path: str) -> None:
        """Step 2: Upload file content directly to the S3 presigned URL (no Drive token)."""
        with open(file_path, "rb") as f:
            response = requests.put(policy_url, data=f.read(), timeout=300)
        if not response.ok:
            logger.error(
                "S3 upload failed (%s) for %s: %s",
                response.status_code,
                file_path,
                response.text[:2000],
            )
        response.raise_for_status()

    def notify_upload_ended(self, item_id: str) -> None:
        """Step 3: notify Drive the upload is complete. A retried timeout/5xx may
        land on an item that's no longer PENDING - see _is_upload_already_processed().
        """
        url = f"{self._base_url()}{self._api_prefix()}/items/{item_id}/upload-ended/"
        max_attempts = settings.DRIVE_RETRY_MAX_ATTEMPTS
        for attempt in range(1, max_attempts + 1):
            retryable_error = None
            try:
                response = requests.post(url, headers=self._headers(), timeout=30)
                response.raise_for_status()
                return
            except HTTPError as http_error:
                if _is_upload_already_processed(http_error):
                    return
                if not _is_server_error(http_error):
                    raise
                retryable_error = http_error
            except (Timeout, RequestsConnectionError) as network_error:
                retryable_error = network_error

            if attempt == max_attempts:
                logger.error(
                    "notify_upload_ended giving up after %s attempt(s): %s",
                    max_attempts,
                    retryable_error,
                )
                raise retryable_error

            wait = settings.DRIVE_RETRY_WAIT_MULTIPLIER**attempt
            logger.info(
                "notify_upload_ended attempt %s/%s failed (%s), retrying in %ss ...",
                attempt,
                max_attempts,
                retryable_error,
                wait,
            )
            time.sleep(wait)

    # --- Sharing ---

    @_retry_on_transient_error
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

    @_retry_on_transient_error
    def share_with_user(self, item_id: str, user_id: str) -> None:
        """Grant owner access. Retried on 5xx: worst case is a duplicate grant,
        cheaper than the user silently ending up with no access."""
        response = requests.post(
            f"{self._base_url()}{self._api_prefix()}/items/{item_id}/accesses/",
            json={"user_id": user_id, "role": "owner"},
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()

    @_retry_on_transient_error
    def invite_by_email(self, item_id: str, email: str) -> None:
        """Invite as owner. Retried on 5xx: worst case is a duplicate email,
        cheaper than the user never being invited."""
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

    @_retry_on_transient_error
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
        self._access_token = decrypt_token(user.oidc_access_token) or None
        self._token_expires_at = user.oidc_token_expires_at

    def _api_prefix(self) -> str:
        return "/api/v1.0"

    @_retry_on_transient_error
    def _refresh(self):
        plaintext_refresh = decrypt_token(self._user.oidc_refresh_token)
        if not plaintext_refresh:
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
                "refresh_token": plaintext_refresh,
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
        self._user.oidc_access_token = encrypt_token(self._access_token)
        if data.get("refresh_token"):
            self._user.oidc_refresh_token = encrypt_token(data["refresh_token"])
        self._user.oidc_token_expires_at = self._token_expires_at
        self._user.save(
            update_fields=[
                "oidc_access_token",
                "oidc_refresh_token",
                "oidc_token_expires_at",
                "updated_at",
            ]
        )
