"""Tests for the Drive user-account policy: who creates the account and how
the destination checks for it."""

from unittest.mock import MagicMock, patch

import pytest

from core.backends.destination import AccountPolicy
from core.destinations.drive.backend import DriveDestinationBackend


def test_account_policy_is_none_in_service_account_mode(settings):
    """The service account creates the items: no user account is involved."""
    settings.DRIVE_AUTH_MODE = "service_account"
    settings.DRIVE_ACCOUNT_POLICY = "user_created"

    assert DriveDestinationBackend().get_account_policy() is AccountPolicy.NONE


@pytest.mark.parametrize("auth_mode", ["user_token", "menshen"])
@pytest.mark.parametrize(
    "policy", [AccountPolicy.USER_CREATED, AccountPolicy.AUTO_CREATED]
)
def test_account_policy_follows_the_setting_in_user_token_mode(
    settings, auth_mode, policy
):
    """In user-token modes (user_token, menshen), DRIVE_ACCOUNT_POLICY says who
    creates the account."""
    settings.DRIVE_AUTH_MODE = auth_mode
    settings.DRIVE_ACCOUNT_POLICY = policy.value

    assert DriveDestinationBackend().get_account_policy() is policy


@pytest.mark.parametrize("auth_mode", ["user_token", "menshen"])
def test_invalid_account_policy_falls_back_to_user_created(settings, auth_mode):
    """A mistyped setting must not break every page listing the destinations."""
    settings.DRIVE_AUTH_MODE = auth_mode
    settings.DRIVE_ACCOUNT_POLICY = "user-created"

    assert DriveDestinationBackend().get_account_policy() is AccountPolicy.USER_CREATED


def test_account_creation_url_is_the_drive_frontend(settings):
    """Logging in to Drive once creates the account."""
    settings.DRIVE_AUTH_MODE = "user_token"
    settings.DRIVE_ACCOUNT_POLICY = "user_created"
    settings.DRIVE_FRONTEND_URL = "https://fichiers.example.com"

    assert (
        DriveDestinationBackend().get_account_creation_url()
        == "https://fichiers.example.com"
    )


@pytest.mark.parametrize(
    "auth_mode, policy",
    [("user_token", "auto_created"), ("service_account", "user_created")],
)
def test_no_account_creation_url_when_the_user_creates_no_account(
    settings, auth_mode, policy
):
    """Only a destination the user must create their account on links to it."""
    settings.DRIVE_AUTH_MODE = auth_mode
    settings.DRIVE_ACCOUNT_POLICY = policy
    settings.DRIVE_FRONTEND_URL = "https://fichiers.example.com"

    assert DriveDestinationBackend().get_account_creation_url() is None


@pytest.mark.parametrize("me, expected", [({"id": "alice-uuid"}, True), (None, False)])
@patch("core.destinations.drive.backend.make_user_drive_backend")
def test_user_account_exists_asks_drive_with_the_user_token(mock_factory, me, expected):
    """The account exists when Drive knows the holder of the user's token."""
    user = MagicMock()
    mock_factory.return_value.get_me.return_value = me

    assert DriveDestinationBackend().user_account_exists(user) is expected
    mock_factory.assert_called_once_with(user)
