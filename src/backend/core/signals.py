"""Signal receivers of the core application."""

import time

from django.dispatch import receiver

from django_structlog import signals


@receiver(signals.bind_extra_request_metadata)
def start_request_timer(request, **kwargs):  # pylint: disable=unused-argument
    """Record when the request started, to log its duration when it finishes."""
    request.started_at = time.monotonic()


@receiver(signals.bind_extra_request_finished_metadata)
def log_request_duration(request, log_kwargs, **kwargs):  # pylint: disable=unused-argument
    """Log the duration of the request with its end."""
    log_kwargs["duration_ms"] = round((time.monotonic() - request.started_at) * 1000)


@receiver(signals.bind_extra_request_finished_metadata)
def log_api_error_name(response, log_kwargs, **kwargs):  # pylint: disable=unused-argument
    """Log the name of the API errors, which tells their cause."""
    data = getattr(response, "data", None)
    if response.status_code >= 400 and isinstance(data, dict) and "error_name" in data:
        log_kwargs["error_name"] = data["error_name"]
