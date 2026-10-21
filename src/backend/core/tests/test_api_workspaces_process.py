"""Tests for WorkspacesProcessAPIView.validation() — pre-flight checks."""

# pylint: disable=protected-access

from unittest.mock import ANY, MagicMock, patch

from django.test import override_settings

import pytest
import requests

from core import factories
from core.api import APIException
from core.api.views.workspaces_process import WorkspacesProcessAPIView
from core.backends.destination import DestinationTokenExpired
from core.destinations.drive.backend import DriveDestinationBackend
from core.retrying_session import _max_attempts_cap

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True, name="drive_account_exists")
def fixture_drive_account_exists():
    """Users have a Drive account unless a test says otherwise (no Drive call)."""
    with patch.object(
        DriveDestinationBackend, "user_account_exists", return_value=True
    ) as mock_exists:
        yield mock_exists


def _make_view():
    return WorkspacesProcessAPIView()


def _workspace_data(workspace, dest_type):
    """Build the `data` dict expected by validation()."""
    return {str(workspace.id): [dest_type]}


# ---------------------------------------------------------------------------
# Drive token pre-flight — synchronous refresh attempt
# ---------------------------------------------------------------------------


@override_settings(DRIVE_AUTH_MODE="user_token")
def test_drive_token_refresh_fails_raises_drive_token_required():
    """Expired session (refresh → 400) must surface as DriveTokenRequired, not 500."""
    user = factories.UserFactory()
    workspace = factories.WorkspaceFactory()

    view = _make_view()
    data = _workspace_data(workspace, "drive")

    with patch("core.api.views.workspaces_process.DriveUserTokenBackend") as mock_cls:
        mock_cls.return_value._get_token.side_effect = DestinationTokenExpired()

        with pytest.raises(APIException) as exc_info:
            view.validation(user, data, [workspace])

    assert exc_info.value.detail["error_name"] == "DriveTokenRequired"


@override_settings(DRIVE_AUTH_MODE="user_token")
def test_drive_token_refresh_unreachable_raises_destination_unavailable():
    """A provider that cannot be reached does not send the user back to log in."""
    user = factories.UserFactory()
    workspace = factories.WorkspaceFactory()

    with patch("core.api.views.workspaces_process.DriveUserTokenBackend") as mock_cls:
        mock_cls.return_value._get_token.side_effect = requests.ConnectionError()

        with pytest.raises(APIException) as exc_info:
            _make_view().validation(
                user, _workspace_data(workspace, "drive"), [workspace]
            )

    assert exc_info.value.detail["error_name"] == "DestinationUnavailable"


@override_settings(DRIVE_AUTH_MODE="user_token")
def test_drive_token_refresh_succeeds_no_exception():
    """Valid (or refreshable) token must not raise — and _get_token is called."""
    user = factories.UserFactory()
    workspace = factories.WorkspaceFactory()

    view = _make_view()
    data = _workspace_data(workspace, "drive")

    with patch("core.api.views.workspaces_process.DriveUserTokenBackend") as mock_cls:
        mock_instance = MagicMock()
        mock_instance._get_token.return_value = "fresh-access-token"
        mock_cls.return_value = mock_instance

        view.validation(user, data, [workspace])  # must not raise

    mock_cls.assert_called_once_with(user)
    mock_instance._get_token.assert_called_once()


@override_settings(DRIVE_AUTH_MODE="service_account")
def test_drive_service_account_mode_skips_token_check():
    """DRIVE_AUTH_MODE=service_account → DriveUserTokenBackend never instantiated."""
    user = factories.UserFactory(oidc_access_token="", oidc_refresh_token="")
    workspace = factories.WorkspaceFactory()

    view = _make_view()
    data = _workspace_data(workspace, "drive")

    with patch("core.api.views.workspaces_process.DriveUserTokenBackend") as mock_cls:
        view.validation(user, data, [workspace])  # must not raise

    mock_cls.assert_not_called()


@override_settings(DRIVE_AUTH_MODE="user_token")
def test_no_drive_destination_skips_token_check():
    """Drive check is skipped when no workspace targets drive."""
    user = factories.UserFactory(oidc_access_token="", oidc_refresh_token="")
    workspace = factories.WorkspaceFactory(source_type="osmose")

    view = _make_view()
    data = _workspace_data(workspace, "resana")

    with patch("core.api.views.workspaces_process.DriveUserTokenBackend") as mock_cls:
        with patch("core.api.views.workspaces_process.ResanaBackend") as mock_resana:
            mock_resana.return_value.fetch_user.return_value = {"id": "u1"}
            view.validation(user, data, [workspace])  # must not raise

    mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Resana source token pre-flight
# ---------------------------------------------------------------------------


@override_settings(DRIVE_AUTH_MODE="service_account")
def test_resana_source_token_missing_raises_resana_token_required():
    """source_type=resana with no stored token must raise ResanaTokenRequired."""
    user = factories.UserFactory()
    workspace = factories.WorkspaceFactory(source_type="resana")

    view = _make_view()
    data = _workspace_data(workspace, "drive")

    with patch("core.api.views.workspaces_process.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.is_connected.return_value = False

        with pytest.raises(APIException) as exc_info:
            view.validation(user, data, [workspace])

    assert exc_info.value.detail["error_name"] == "ResanaTokenRequired"


@override_settings(DRIVE_AUTH_MODE="service_account")
def test_resana_source_token_present_does_not_raise():
    """source_type=resana with a valid token must not raise."""
    user = factories.UserFactory()
    workspace = factories.WorkspaceFactory(source_type="resana")

    view = _make_view()
    data = _workspace_data(workspace, "drive")

    with patch("core.api.views.workspaces_process.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.is_connected.return_value = True
        view.validation(user, data, [workspace])  # must not raise

    mock_tm.assert_called_once_with(user)


def test_non_resana_source_skips_token_check():
    """source_type != resana must not trigger ResanaTokenManager."""
    user = factories.UserFactory()
    workspace = factories.WorkspaceFactory(source_type="osmose")

    view = _make_view()
    data = _workspace_data(workspace, "drive")

    with patch("core.api.views.workspaces_process.ResanaTokenManager") as mock_tm:
        with patch("core.api.views.workspaces_process.DriveUserTokenBackend"):
            view.validation(user, data, [workspace])

    mock_tm.assert_not_called()


# ---------------------------------------------------------------------------
# Token status logged before the pre-flight checks
# ---------------------------------------------------------------------------


@override_settings(DRIVE_AUTH_MODE="user_token")
def test_validation_logs_token_status_before_failing_check():
    """The tokens are logged as found, even when a check then refuses the launch."""
    user = factories.UserFactory()
    workspace = factories.WorkspaceFactory(source_type="resana")

    view = _make_view()
    data = _workspace_data(workspace, "drive")

    with (
        patch("core.api.views.workspaces_process.log_token_status") as mock_log,
        patch("core.api.views.workspaces_process.DriveUserTokenBackend"),
        patch("core.api.views.workspaces_process.ResanaTokenManager") as mock_tm,
    ):
        mock_tm.return_value.is_connected.return_value = False

        with pytest.raises(APIException):
            view.validation(user, data, [workspace])

    mock_log.assert_called_once_with(
        ANY, "Before migration launch", user, resana=True, drive=True
    )


def test_validation_logs_no_token_for_unused_services():
    """Neither Resana nor Drive tokens are flagged when the migration doesn't use them."""
    user = factories.UserFactory()
    workspace = factories.WorkspaceFactory(source_type="osmose")

    view = _make_view()
    data = _workspace_data(workspace, "resana")

    with (
        patch("core.api.views.workspaces_process.log_token_status") as mock_log,
        patch("core.api.views.workspaces_process.ResanaBackend") as mock_resana,
    ):
        mock_resana.return_value.fetch_user.return_value = {"id": "u1"}
        view.validation(user, data, [workspace])

    mock_log.assert_called_once_with(
        ANY, "Before migration launch", user, resana=False, drive=False
    )


# ---------------------------------------------------------------------------
# Destination user account pre-flight
# ---------------------------------------------------------------------------


@override_settings(DRIVE_AUTH_MODE="user_token", DRIVE_ACCOUNT_POLICY="user_created")
def test_missing_drive_account_raises_account_not_found(drive_account_exists):
    """A user who never logged in to Drive cannot start a Drive migration."""
    drive_account_exists.return_value = False
    user = factories.UserFactory()
    workspace = factories.WorkspaceFactory(source_type="osmose")

    with patch("core.api.views.workspaces_process.DriveUserTokenBackend"):
        with pytest.raises(APIException) as exc_info:
            _make_view().validation(
                user, _workspace_data(workspace, "drive"), [workspace]
            )

    assert exc_info.value.detail["error_name"] == "AccountNotFound"
    drive_account_exists.assert_called_once_with(user)


@override_settings(DRIVE_AUTH_MODE="user_token", DRIVE_ACCOUNT_POLICY="auto_created")
def test_auto_created_drive_account_is_not_checked(drive_account_exists):
    """Drive creating the account itself, its absence does not block."""
    drive_account_exists.return_value = False
    user = factories.UserFactory()
    workspace = factories.WorkspaceFactory(source_type="osmose")

    with patch("core.api.views.workspaces_process.DriveUserTokenBackend"):
        _make_view().validation(
            user, _workspace_data(workspace, "drive"), [workspace]
        )  # must not raise

    drive_account_exists.assert_not_called()


@override_settings(DRIVE_AUTH_MODE="user_token", DRIVE_ACCOUNT_POLICY="user_created")
@pytest.mark.parametrize(
    "error, error_name",
    [
        (DestinationTokenExpired(), "DriveTokenRequired"),
        (requests.ConnectionError(), "DestinationUnavailable"),
    ],
)
def test_failed_account_check_raises_a_named_error(
    drive_account_exists, error, error_name
):
    """A check that cannot conclude is reported, not turned into a server error."""
    drive_account_exists.side_effect = error
    user = factories.UserFactory()
    workspace = factories.WorkspaceFactory(source_type="osmose")

    with patch("core.api.views.workspaces_process.DriveUserTokenBackend"):
        with pytest.raises(APIException) as exc_info:
            _make_view().validation(
                user, _workspace_data(workspace, "drive"), [workspace]
            )

    assert exc_info.value.detail["error_name"] == error_name


@override_settings(DRIVE_AUTH_MODE="user_token", DRIVE_ACCOUNT_POLICY="user_created")
def test_account_check_tries_the_destination_once(drive_account_exists):
    """Inside the web request, the account check is not retried."""
    drive_account_exists.side_effect = lambda user: _max_attempts_cap.get() == 1
    user = factories.UserFactory()
    workspace = factories.WorkspaceFactory(source_type="osmose")

    with patch("core.api.views.workspaces_process.DriveUserTokenBackend"):
        _make_view().validation(
            user, _workspace_data(workspace, "drive"), [workspace]
        )  # must not raise: the check only passes under the cap
