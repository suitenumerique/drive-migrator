"""Checks of the user accounts destinations require, made within a web request."""

import logging

from requests.exceptions import RequestException

from core.api import APIException
from core.backends.destination import DestinationTokenExpired
from core.retrying_session import limited_retries

logger = logging.getLogger(__name__)


def check_user_account(dest, user) -> bool:
    """Ask the destination whether user has their account, within a web request:
    each call is tried once, and a check that cannot conclude raises a named error."""
    try:
        # Retrying a hanging destination would outlast the server's timeout.
        with limited_retries(max_attempts=1):
            return dest.user_account_exists(user)
    except DestinationTokenExpired as error:
        raise APIException("DriveTokenRequired") from error
    except RequestException as error:
        # A misconfiguration (e.g. a refused client) looks the same as an outage.
        logger.warning("Could not check the user account on %s: %s", dest.name, error)
        raise APIException("DestinationUnavailable") from error
