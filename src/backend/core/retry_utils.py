"""Shared tenacity helpers for backends that retry on transient network errors."""


def log_final_failure_and_reraise(logger):
    """Return a tenacity retry_error_callback bound to the given logger.

    Individual retries are recoverable and expected to be logged at INFO by the
    caller's before_sleep hook; this callback runs once every attempt is exhausted,
    which is the one point where the call has definitively failed, so it logs an
    ERROR before reraising the original exception.
    """

    def _callback(retry_state):
        error = retry_state.outcome.exception()
        logger.error(
            "%s giving up after %s attempt(s): %s",
            retry_state.fn.__name__,
            retry_state.attempt_number,
            error,
        )
        raise error

    return _callback
