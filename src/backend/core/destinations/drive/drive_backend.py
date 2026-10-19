"""DriveBackend — HTTP client for La Suite Drive API."""

import logging
import time
import uuid
from datetime import timedelta
from enum import StrEnum

from django.conf import settings
from django.utils import timezone

from celery.utils.log import get_task_logger
from requests.auth import AuthBase
from requests.exceptions import HTTPError, RequestException
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    wait_exponential,
)

from core.encryption import decrypt_token, encrypt_token
from core.retry_utils import log_final_failure_and_reraise
from core.retrying_session import RetryingSession, is_transient_error
from core.utils import (
    describe_access_expiry,
    describe_oauth_error,
    describe_request_error,
)

logger = get_task_logger(__name__)

_UPLOAD_STATE_NOT_PENDING = "item_upload_state_not_pending"
_EXISTING_ID = "item_create_existing_id"
# Drive caps page_size with its MAX_PAGE_SIZE setting (200 by default).
_LIST_PAGE_SIZE = 200


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


# Retries item creation on a transient network error or a Drive-side 5xx. Other
# calls are retried by the backend's RetryingSession; creation can't be, since it
# must check whether an ambiguously-failed POST created the item before replaying.
_retry_on_transient_error = retry(
    retry=retry_if_exception(is_transient_error) | retry_if_exception(_is_server_error),
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


def _is_blocked_upstream(error: HTTPError) -> bool:
    """Drive API errors are always JSON, so a non-JSON 403 comes upfront."""
    response = error.response
    if response is None or response.status_code != 403:
        return False
    try:
        response.json()
    except ValueError:
        return True
    return False


class FileRejection(StrEnum):
    """Known refusals of a file itself: the file is skipped, the migration goes on."""

    EXTENSION_NOT_ALLOWED = "item_create_file_extension_not_allowed"
    TYPE_NOT_ALLOWED = "file_type_not_allowed"
    SIZE_EXCEEDED = "file_size_exceeded"
    SIZE_MISMATCH = "file_size_mismatch"
    # Something in front of Drive (e.g. a WAF) may block some file names, like
    # "index.php.jpg": only the creation request carries the file name.
    BLOCKED_UPSTREAM = "blocked_upstream"

    @property
    def at_creation_only(self) -> bool:
        """Only the creation request carries the file name."""
        return self is FileRejection.BLOCKED_UPSTREAM

    def matches(self, error: HTTPError) -> bool:
        if self is FileRejection.BLOCKED_UPSTREAM:
            return _is_blocked_upstream(error)
        return _has_error_code(error, 400, self.value)


def get_file_rejection(
    error: HTTPError, at_creation: bool = False
) -> FileRejection | None:
    """Return the known refusal of the file itself, None for any other error."""
    return next(
        (
            rejection
            for rejection in FileRejection
            if (at_creation or not rejection.at_creation_only)
            and rejection.matches(error)
        ),
        None,
    )


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


def describe_drive_tokens(user) -> str:
    """Describe the stored ProConnect tokens and their lifetime for logs, never
    their value."""
    access = "access token missing"
    if user.oidc_access_token:
        access = f"access token present, {describe_access_expiry(user.oidc_token_expires_at)}"
    refresh = (
        "refresh token present" if user.oidc_refresh_token else "refresh token missing"
    )
    return f"Drive (ProConnect) tokens for user {user.id}: {access}; {refresh}"


class _BearerTokenAuth(AuthBase):
    """Set the bearer token when each attempt is prepared, so a retry after a
    long timeout sends a refreshed token instead of one that expired meanwhile."""

    def __init__(self, get_token):
        self._get_token = get_token

    def __call__(self, request):
        request.headers["Authorization"] = f"Bearer {self._get_token()}"
        return request


class DriveBackend:
    """Base HTTP client for La Suite Drive API.

    Subclasses implement _refresh() to select the auth strategy.
    """

    API_PREFIX: str = "/external_api/v1.0"

    def __init__(self):
        self._access_token = None
        self._token_expires_at = None
        self.session = RetryingSession("DRIVE", logger)
        # Per call, not on the session: the S3 upload must not receive the token.
        self._auth = _BearerTokenAuth(self._get_token)

    @property
    def api_root_url(self) -> str:
        """Get API root URL."""
        return f"{settings.DRIVE_API_BASE_URL}{DriveBackend.API_PREFIX}"

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

    # --- Folder operations ---

    def create_folder(self, title: str) -> dict:
        """Create a root folder in Drive. Returns the item dict (includes 'id')."""
        url = f"{self.api_root_url}/items/"
        payload = {"type": "folder", "title": title}
        return self._create_item(url, payload)

    def create_subfolder(self, title: str, parent_id: str) -> dict:
        """Create a child folder inside an existing Drive folder."""
        url = f"{self.api_root_url}/items/{parent_id}/children/"
        payload = {"type": "folder", "title": title}
        return self._create_item(url, payload)

    # --- File upload (3-step) ---

    def create_file_item(
        self, filename: str, parent_id: str, size: int, item_id: str | None = None
    ) -> dict:
        """Step 1: Create a file item. Returns item dict including S3 presigned URL in 'policy'.
        Pass item_id to know the item's id before the request is sent. Recent Drive
        versions require size and check the uploaded file against it, older ones
        ignore it."""
        url = f"{self.api_root_url}/items/{parent_id}/children/"
        payload = {"type": "file", "filename": filename, "size": size}
        item = self._create_item(url, payload, item_id=item_id)
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

    def _delete_item(self, item_id: str) -> None:
        """DELETE /items/{id}/ (soft delete)."""
        url = f"{self.api_root_url}/items/{item_id}/"
        response = self.session.delete(
            url, auth=self._auth, timeout=30, idempotent=True
        )
        response.raise_for_status()

    def _create_item(self, url: str, payload: dict, item_id: str | None = None) -> dict:
        """POST with a client-generated id, retried via tenacity on 5xx/network
        errors or a Drive id conflict: creation isn't idempotent (Drive silently
        renames title duplicates), so on a retryable failure we check for the item
        by id before giving up, instead of duplicating it or failing a write that
        actually succeeded. See #208."""
        item_id = item_id or str(uuid.uuid4())
        payload = {**payload, "id": item_id}

        @_retry_on_transient_error
        def do_create_item():
            try:
                response = self.session.post(
                    url, json=payload, auth=self._auth, timeout=30
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
            except RequestException as network_error:
                if not is_transient_error(network_error):
                    raise
                existing_item = self._get_item_if_exists_or_none(item_id)
                if existing_item is not None:
                    return existing_item
                raise

        return do_create_item()

    def get_item(self, item_id: str) -> dict | None:
        """Return the item, or None if Drive answers 404."""
        return self._get_item_if_exists(item_id)

    def list_children(self, item_id: str) -> list[dict]:
        """Return all children of a folder, following pagination. Drive hides
        items still in PENDING upload state from this listing."""
        url = f"{self.api_root_url}/items/{item_id}/children/"
        params = {"page_size": _LIST_PAGE_SIZE}
        children = []
        while url:
            response = self.session.get(url, params=params, auth=self._auth, timeout=30)
            response.raise_for_status()
            data = response.json()
            children.extend(data.get("results", []))
            url = data.get("next")
            # "next" already carries the query string.
            params = None
        return children

    def _get_item_if_exists(
        self, item_id: str, idempotent: bool | None = None
    ) -> dict | None:
        """GET /items/{id}/, treating a 404 as 'not created yet' rather than an error."""
        url = f"{self.api_root_url}/items/{item_id}/"
        response = self.session.get(
            url, auth=self._auth, timeout=30, idempotent=idempotent
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def _get_item_if_exists_or_none(self, item_id: str) -> dict | None:
        """Like _get_item_if_exists(), but only swallows a transient failure (5xx,
        network) of the check itself, so the caller retries the POST. A permanent
        failure (401/403) is re-raised instead of masked. Not retried by the
        session: the create loop calling it already retries."""
        try:
            return self._get_item_if_exists(item_id, idempotent=False)
        except HTTPError as http_error:
            if not _is_server_error(http_error):
                raise
            logger.info(
                "existence check for item %s failed (%s), will retry",
                item_id,
                http_error,
            )
            return None
        except RequestException as network_error:
            if not is_transient_error(network_error):
                raise
            logger.info(
                "existence check for item %s failed (%s), will retry",
                item_id,
                network_error,
            )
            return None

    def upload_to_s3(self, policy_url: str, file_path: str) -> None:
        """Step 2: Upload file content directly to the S3 presigned URL (no Drive token)."""
        with open(file_path, "rb") as f:
            response = self.session.put(
                policy_url, data=f.read(), timeout=300, idempotent=True
            )
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
        url = f"{self.api_root_url}/items/{item_id}/upload-ended/"
        max_attempts = settings.DRIVE_RETRY_MAX_ATTEMPTS
        for attempt in range(1, max_attempts + 1):
            retryable_error = None
            try:
                response = self.session.post(url, auth=self._auth, timeout=30)
                response.raise_for_status()
                return
            except HTTPError as http_error:
                if _is_upload_already_processed(http_error):
                    return
                if not _is_server_error(http_error):
                    raise
                retryable_error = http_error
            except RequestException as network_error:
                if not is_transient_error(network_error):
                    raise
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

    def find_user_by_email(self, email: str) -> dict | None:
        """Resolve an email to a Drive user dict. Returns None if not found."""
        response = self.session.get(
            f"{self.api_root_url}/users/",
            params={"q": email},
            auth=self._auth,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        results = data if isinstance(data, list) else data.get("results", [])
        # The search is fuzzy: only a user with this very email is them.
        return next(
            (
                user
                for user in results
                if (user.get("email") or "").casefold() == email.casefold()
            ),
            None,
        )

    def share_with_user(self, item_id: str, user_id: str) -> None:
        """Grant owner access. Retried on 5xx: worst case is a duplicate grant,
        cheaper than the user silently ending up with no access."""
        response = self.session.post(
            f"{self.api_root_url}/items/{item_id}/accesses/",
            json={"user_id": user_id, "role": "owner"},
            auth=self._auth,
            timeout=30,
            idempotent=True,
        )
        response.raise_for_status()

    def invite_by_email(self, item_id: str, email: str) -> None:
        """Invite as owner. Retried on 5xx: worst case is a duplicate email,
        cheaper than the user never being invited."""
        response = self.session.post(
            f"{self.api_root_url}/items/{item_id}/invitations/",
            json={"email": email, "role": "owner"},
            auth=self._auth,
            timeout=30,
            idempotent=True,
        )
        response.raise_for_status()


class DriveServiceAccountBackend(DriveBackend):
    """Uses OAuth2 client_credentials grant. Targets /external_api/v1.0/."""

    def _refresh(self):
        response = self.session.post(
            settings.DRIVE_OIDC_TOKEN_ENDPOINT,
            data={
                "grant_type": "client_credentials",
                "client_id": settings.DRIVE_OIDC_CLIENT_ID,
                "client_secret": settings.DRIVE_OIDC_CLIENT_SECRET,
                "scope": "openid email",
            },
            timeout=30,
            idempotent=True,
        )
        response.raise_for_status()
        data = response.json()
        self._access_token = data["access_token"]
        expires_in = data.get("expires_in")
        self._token_expires_at = (
            timezone.now() + timedelta(seconds=expires_in) if expires_in else None
        )


class DriveUserTokenBackend(DriveBackend):
    """Uses the authenticated user's ProConnect token. Targets /external_api/v1.0/.

    The token is seeded from user.oidc_access_token and refreshed via
    user.oidc_refresh_token when it expires. Updated tokens are persisted
    back to the user model so subsequent Celery tasks can reuse them.
    """

    def __init__(self, user):
        super().__init__()
        self._user = user
        self._access_token = decrypt_token(user.oidc_access_token) or None
        self._token_expires_at = user.oidc_token_expires_at

    def _refresh(self):
        logger.info(
            "Drive (ProConnect) token refresh for user %s: %s, "
            "refreshing with the refresh token",
            self._user.id,
            "access token missing"
            if self._access_token is None
            else f"access token {describe_access_expiry(self._token_expires_at)}",
        )
        plaintext_refresh = decrypt_token(self._user.oidc_refresh_token)
        if not plaintext_refresh:
            self._log_refresh_failure("no refresh token stored")
            raise RuntimeError(
                f"No refresh token stored for user {self._user.email}. "
                "Cannot refresh the ProConnect token for Drive migration."
            )
        try:
            response = self.session.post(
                settings.OIDC_OP_TOKEN_ENDPOINT,
                data={
                    "grant_type": "refresh_token",
                    "client_id": settings.OIDC_RP_CLIENT_ID,
                    "client_secret": settings.OIDC_RP_CLIENT_SECRET,
                    "refresh_token": plaintext_refresh,
                },
                timeout=30,
                idempotent=True,
            )
            response.raise_for_status()
        except HTTPError as error:
            self._log_refresh_failure(
                f"ProConnect returned {describe_oauth_error(error)}"
            )
            raise
        except RequestException as error:
            self._log_refresh_failure(
                f"ProConnect request failed ({describe_request_error(error)})"
            )
            raise
        try:
            data = response.json()
            access_token = data["access_token"]
        except (ValueError, KeyError, TypeError) as error:
            self._log_refresh_failure(
                f"ProConnect returned no access token ({type(error).__name__})"
            )
            raise
        expires_in = data.get("expires_in")
        self._access_token = access_token
        self._token_expires_at = (
            timezone.now() + timedelta(seconds=expires_in) if expires_in else None
        )
        self._user.oidc_access_token = encrypt_token(self._access_token)
        new_refresh = data.get("refresh_token")
        if new_refresh:
            self._user.oidc_refresh_token = encrypt_token(new_refresh)
        self._user.oidc_token_expires_at = self._token_expires_at
        self._user.save(
            update_fields=[
                "oidc_access_token",
                "oidc_refresh_token",
                "oidc_token_expires_at",
                "updated_at",
            ]
        )
        logger.info(
            "Drive (ProConnect) token refresh succeeded for user %s: "
            "new access token %s, refresh token %s",
            self._user.id,
            describe_access_expiry(self._token_expires_at),
            "rotated"
            if new_refresh and new_refresh != plaintext_refresh
            else "unchanged",
        )

    def _log_refresh_failure(self, reason: str) -> None:
        """Transient failures are retried, and logged, by the session."""
        logger.warning(
            "Drive (ProConnect) token refresh failed for user %s: %s",
            self._user.id,
            reason,
        )
