"""HTTP client for the Interstis GED API."""

import logging

from django.conf import settings

from celery.utils.log import get_task_logger
from requests.exceptions import HTTPError
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    wait_exponential,
)

from core.retry_utils import log_final_failure_and_reraise
from core.retrying_session import RetryingSession, is_transient_error

logger = get_task_logger(__name__)

PAGE_SIZE = 500
CHUNK_SIZE = 8192


def _is_server_error(exc: BaseException) -> bool:
    """A 5xx response is a transient failure on Interstis' side, safe to retry."""
    return (
        isinstance(exc, HTTPError)
        and exc.response is not None
        and exc.response.status_code >= 500
    )


def _stop_after_configured_attempts(retry_state) -> bool:
    """Read RESANA_RETRY_MAX_ATTEMPTS at call time, not decoration time, so it
    stays overridable per-test/per-environment like every other setting here."""
    return retry_state.attempt_number >= settings.RESANA_RETRY_MAX_ATTEMPTS


def _wait_configured_backoff(retry_state) -> float:
    """Same rationale as _stop_after_configured_attempts: read settings live."""
    return wait_exponential(
        multiplier=settings.RESANA_RETRY_WAIT_MULTIPLIER,
        min=settings.RESANA_RETRY_WAIT_MIN,
    )(retry_state)


# Retries the whole streamed download: body read errors (timeouts, connections
# cut mid-stream) are raised by iter_content(), after the session's request()
# has returned, out of RetryingSession's reach.
_retry_on_transient_error = retry(
    retry=retry_if_exception(is_transient_error) | retry_if_exception(_is_server_error),
    stop=_stop_after_configured_attempts,
    wait=_wait_configured_backoff,
    before_sleep=before_sleep_log(logger, logging.INFO),
    retry_error_callback=log_final_failure_and_reraise(logger),
)


class InterstisClient:
    def __init__(self, token: str):
        self.token = token
        self.session = RetryingSession("RESANA", logger)
        self.session.headers["Authorization"] = f"Bearer {token}"

    def _get_paginated(self, url: str, extra_params: dict | None = None) -> list[dict]:
        results = []
        page = 1
        while True:
            params = {"page": page, "itemsPerPage": PAGE_SIZE, **(extra_params or {})}
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            results.extend(data.get("hydra:member", []))
            if len(results) >= data.get("hydra:totalItems", 0):
                break
            page += 1
        return results

    def get_workspaces(self) -> list[dict]:
        return self._get_paginated(settings.RESANA_API_ENDPOINT + "/api/workspaces")

    def explore(self, uuid: str) -> list[dict]:
        return self._get_paginated(
            f"{settings.RESANA_API_ENDPOINT}/api/targets/{uuid}/explore"
        )

    @_retry_on_transient_error
    def download_file(self, uuid: str, destination_path: str) -> None:
        url = f"{settings.RESANA_API_ENDPOINT}/api/targets/{uuid}/download"
        with self.session.get(
            url, stream=True, timeout=60, idempotent=False
        ) as response:
            response.raise_for_status()
            with open(destination_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    f.write(chunk)
