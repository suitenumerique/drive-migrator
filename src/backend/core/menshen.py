"""Menshen (RFC 8693) token exchange client, shared by destination backends."""

import functools
import json

from django.conf import settings

import requests
from celery.utils.log import get_task_logger
from menshen_client import (
    APIError,
    Configuration,
    MenshenClientError,
    ResponseParsingError,
    ServerUnavailableError,
    TokenExchangeClient,
    TokenExchangeRequest,
    TokenType,
)
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import HTTPError

from core.backends.destination import DestinationTokenExpired
from core.utils import describe_oauth_failure, is_invalid_grant_payload

logger = get_task_logger(__name__)


@functools.cache
def get_menshen_client() -> TokenExchangeClient:
    """One long-lived client per process, as the Menshen client recommends."""
    return TokenExchangeClient(
        config=Configuration(
            client_id=settings.MENSHEN_CLIENT_ID,
            client_secret=settings.MENSHEN_CLIENT_SECRET,
            server_root_url=settings.MENSHEN_SERVER_ROOT_URL,
            requests_timeout=settings.MENSHEN_REQUESTS_TIMEOUT,
        )
    )


def exchange_token(user, subject_token: str, audience: str, scope: str):
    """Exchange the user's ProConnect token with Menshen (RFC 8693).

    Failures are raised as the request errors callers already handle.
    """
    try:
        return get_menshen_client().exchange(
            TokenExchangeRequest(
                subject_token=subject_token,
                subject_token_type=TokenType.ACCESS_TOKEN,
                audience=audience or None,
                scope=scope or None,
            )
        )
    except APIError as error:
        payload = _menshen_error_payload(error)
        _log_exchange_failure(
            user,
            audience,
            f"Menshen returned {describe_oauth_failure(error.status_code, payload)}",
        )
        # Menshen refuses an expired or revoked ProConnect token: logging in
        # again gives a fresh one.
        if is_invalid_grant_payload(error.status_code, payload):
            raise DestinationTokenExpired(
                f"Menshen refused the ProConnect token of user {user.email}."
            ) from error
        # Raised as requests errors, which callers already handle: 5xx are
        # transient, other refusals are configuration errors. The translated
        # HTTPError carries Menshen's response, so callers reading it (status,
        # body) do not break.
        if error.status_code >= 500:
            raise RequestsConnectionError(
                f"Menshen returned {error.status_code}"
            ) from error
        raise HTTPError(
            f"Menshen returned {error.status_code}",
            response=_menshen_error_response(error),
        ) from error
    except ServerUnavailableError as error:
        _log_exchange_failure(
            user, audience, f"Menshen request failed ({type(error).__name__})"
        )
        raise RequestsConnectionError(
            f"Menshen request failed ({type(error).__name__})"
        ) from error
    except ResponseParsingError as error:
        # Menshen answered something the client cannot parse (unexpected
        # body, wrong URL): a server-side problem, raised as the request
        # errors callers already handle.
        _log_exchange_failure(user, audience, "Menshen returned an invalid response")
        raise RequestsConnectionError(
            f"Menshen request failed ({type(error).__name__})"
        ) from error
    except MenshenClientError as error:
        _log_exchange_failure(
            user, audience, f"Menshen client error ({type(error).__name__})"
        )
        raise


def _log_exchange_failure(user, audience: str, reason: str) -> None:
    logger.warning(
        "Menshen token exchange for audience %r failed for user %s: %s",
        audience,
        user.id,
        reason,
    )


def _menshen_error_payload(error: APIError):
    """The decoded JSON body of a Menshen error response, None when not JSON."""
    try:
        return json.loads(error.body)
    except ValueError:
        return None


def _menshen_error_response(error: APIError) -> requests.Response:
    """A minimal requests.Response carrying Menshen's error body, so the
    translated HTTPError behaves like the ones callers already handle."""
    response = requests.Response()
    response.status_code = error.status_code
    response.url = error.url
    response.headers["Content-Type"] = "application/json"
    # pylint: disable-next=protected-access
    response._content = error.body.encode()  # noqa: SLF001
    return response
