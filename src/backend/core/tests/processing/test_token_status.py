"""Tests for log_token_status()."""

from unittest.mock import MagicMock, call, patch

from django.test import override_settings

import pytest

from core import factories
from core.processing.token_status import log_token_status

pytestmark = pytest.mark.django_db


@override_settings(DRIVE_AUTH_MODE="user_token")
def test_log_token_status_logs_resana_and_drive_tokens():
    """Each service the migration relies on gets its own log line."""
    user = factories.UserFactory()
    logger = MagicMock()

    log_token_status(logger, "Before migration launch", user, resana=True, drive=True)

    assert logger.info.call_args_list == [
        call(
            "%s: %s",
            "Before migration launch",
            f"Resana tokens for user {user.id}: "
            "access token missing; offline token missing",
        ),
        call(
            "%s: %s",
            "Before migration launch",
            f"Drive (ProConnect) tokens for user {user.id}: "
            "access token missing; refresh token missing",
        ),
    ]


@override_settings(DRIVE_AUTH_MODE="service_account")
def test_log_token_status_skips_drive_in_service_account_mode():
    """The service account doesn't use the user's tokens: nothing to log for Drive."""
    user = factories.UserFactory()
    logger = MagicMock()

    log_token_status(logger, "Before migration launch", user, resana=False, drive=True)

    logger.info.assert_not_called()


@override_settings(DRIVE_AUTH_MODE="user_token")
def test_log_token_status_logs_nothing_when_no_service_is_involved():
    """No line is logged for a service the migration doesn't use."""
    user = factories.UserFactory()
    logger = MagicMock()

    log_token_status(logger, "Before migration launch", user, resana=False, drive=False)

    logger.info.assert_not_called()


@override_settings(DRIVE_AUTH_MODE="user_token")
def test_log_token_status_never_raises():
    """A token that can't be described is reported, and the next service still logged."""
    user = factories.UserFactory()
    logger = MagicMock()

    with patch(
        "core.processing.token_status.ResanaTokenManager.describe_tokens",
        side_effect=TypeError("boom"),
    ):
        log_token_status(
            logger, "Before migration launch", user, resana=True, drive=True
        )

    logger.warning.assert_called_once_with(
        "%s: unable to describe the %s tokens of user %s (%s)",
        "Before migration launch",
        "Resana",
        user.id,
        "TypeError",
    )
    assert "Drive (ProConnect) tokens" in logger.info.call_args.args[2]
