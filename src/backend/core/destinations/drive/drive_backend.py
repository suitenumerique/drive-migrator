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
    """A 5xx means Drive's view raised after fully processing the request. Drive's
    item-creation endpoint isn't wrapped in a transaction, so unlike a client-side
    network error, this doesn't guarantee the write was rolled back - the caller
    is responsible for checking for a partially-applied write before replaying a
    call that isn't naturally idempotent (see DriveBackend._create_item_with_retry).
    4xx errors are permanent/client errors and must keep failing fast, not retried."""
    return (
        isinstance(error, HTTPError)
        and error.response is not None
        and error.response.status_code >= 500
    )


# Applied to calls that are safe to retry on a transient network error or a Drive-side
# 5xx: either the request never reached Drive, or it did and was rolled back there.
_retry_on_transient_error = retry(
    retry=retry_if_exception_type((Timeout, RequestsConnectionError))
    | retry_if_exception(_is_server_error),
    stop=_stop_after_configured_attempts,
    wait=_wait_configured_backoff,
    before_sleep=before_sleep_log(logger, logging.INFO),
    retry_error_callback=log_final_failure_and_reraise(logger),
)


def _is_upload_already_processed(error: HTTPError) -> bool:
    """Detect the item_upload_state_not_pending error.

    A ReadTimeout can happen after Drive already processed the request but before
    we received the response. Retrying then hits this 400 error, which means the
    original call actually succeeded and should be treated as such.
    """
    response = error.response
    if response is None or response.status_code != 400:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    return any(
        error_detail.get("code") == _UPLOAD_STATE_NOT_PENDING
        for error_detail in payload.get("errors", [])
    )


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
        item_id = str(uuid.uuid4())

        def do_post():
            response = requests.post(
                f"{self._base_url()}{self._api_prefix()}/items/",
                json={"id": item_id, "type": "folder", "title": title},
                headers=self._headers(),
                timeout=30,
            )
            response.raise_for_status()
            return response.json()

        return self._create_item_with_retry(item_id, do_post)

    def create_subfolder(self, title: str, parent_id: str) -> dict:
        """Create a child folder inside an existing Drive folder."""
        item_id = str(uuid.uuid4())

        def do_post():
            response = requests.post(
                f"{self._base_url()}{self._api_prefix()}/items/{parent_id}/children/",
                json={"id": item_id, "type": "folder", "title": title},
                headers=self._headers(),
                timeout=30,
            )
            response.raise_for_status()
            return response.json()

        return self._create_item_with_retry(item_id, do_post)

    # --- File upload (3-step) ---

    def create_file_item(self, filename: str, parent_id: str) -> dict:
        """Step 1: Create a file item. Returns item dict including S3 presigned URL in 'policy'."""
        item_id = str(uuid.uuid4())

        def do_post():
            response = requests.post(
                f"{self._base_url()}{self._api_prefix()}/items/{parent_id}/children/",
                json={"id": item_id, "type": "file", "filename": filename},
                headers=self._headers(),
                timeout=30,
            )
            response.raise_for_status()
            return response.json()

        return self._create_item_with_retry(item_id, do_post)

    def _create_item_with_retry(self, item_id: str, do_post) -> dict:
        """Run an item-creation POST, retrying on a transient network error or 5xx.

        Item creation isn't naturally idempotent - and Drive silently renames a
        same-title duplicate (e.g. "docs_01") rather than rejecting it - so on a
        retryable failure we can't just replay the POST blindly: we first check
        whether `item_id` (generated by the caller and sent in the request body)
        already exists, in case Drive actually created it before the error. See #208.
        """
        max_attempts = settings.DRIVE_RETRY_MAX_ATTEMPTS
        for attempt in range(1, max_attempts + 1):
            try:
                return do_post()
            except HTTPError as http_error:
                if not _is_server_error(http_error):
                    raise
                retryable_error = http_error
            except (Timeout, RequestsConnectionError) as network_error:
                retryable_error = network_error

            existing_item = self._get_item_if_exists_or_none(item_id)
            if existing_item is not None:
                return existing_item

            if attempt == max_attempts:
                logger.error(
                    "create item giving up after %s attempt(s): %s",
                    max_attempts,
                    retryable_error,
                )
                raise retryable_error

            wait = settings.DRIVE_RETRY_WAIT_MULTIPLIER**attempt
            logger.info(
                "create item attempt %s/%s failed (%s), retrying in %ss ...",
                attempt,
                max_attempts,
                retryable_error,
                wait,
            )
            time.sleep(wait)
        raise AssertionError("unreachable: the loop above always returns or raises")

    def _get_item_if_exists(self, item_id: str) -> dict | None:
        """GET /items/{id}/, treating a 404 as 'not created yet' rather than an error."""
        response = requests.get(
            f"{self._base_url()}{self._api_prefix()}/items/{item_id}/",
            headers=self._headers(),
            timeout=30,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def _get_item_if_exists_or_none(self, item_id: str) -> dict | None:
        """Same as _get_item_if_exists(), but a failing check must not abort the
        creation retry loop: if the check itself hits a transient error, we simply
        don't know yet whether Drive created the item, so fall back to retrying the
        creation POST instead of giving up on the whole operation."""
        try:
            return self._get_item_if_exists(item_id)
        except (HTTPError, Timeout, RequestsConnectionError) as error:
            logger.info(
                "existence check for item %s failed (%s), will retry", item_id, error
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
        """Step 3: Notify Drive that the S3 upload is complete.

        A ReadTimeout, or a 5xx from Drive, doesn't tell us whether Drive actually
        processed the request, so a retry may land on an item that's no longer
        PENDING - see _is_upload_already_processed().
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
        """Grant owner access to an existing Drive user.

        Retried on 5xx despite the lack of a partially-applied-write check (unlike
        item creation): worst case is a duplicate access grant, which is much less
        costly than the migrated user silently ending up with no access at all.
        """
        response = requests.post(
            f"{self._base_url()}{self._api_prefix()}/items/{item_id}/accesses/",
            json={"user_id": user_id, "role": "owner"},
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()

    @_retry_on_transient_error
    def invite_by_email(self, item_id: str, email: str) -> None:
        """Invite a user not yet registered in Drive as owner.

        Retried on 5xx despite the lack of a partially-applied-write check (unlike
        item creation): worst case is a duplicate invitation email, which is much
        less costly than the migrated user silently never being invited at all.
        """
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
