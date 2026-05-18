"""Tests for WorkspacesProcessAPIView.validation() — Drive token pre-flight check."""

from unittest.mock import MagicMock, patch

from django.test import override_settings

import pytest
import requests

from core import factories
from core.api import APIException
from core.api.views.workspaces_process import WorkspacesProcessAPIView

pytestmark = pytest.mark.django_db


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

    with patch(
        "core.api.views.workspaces_process.DriveUserTokenBackend"
    ) as mock_cls:
        mock_cls.return_value._get_token.side_effect = requests.HTTPError("400")

        with pytest.raises(APIException) as exc_info:
            view.validation(user, data, [workspace])

    assert exc_info.value.detail["error_name"] == "DriveTokenRequired"


@override_settings(DRIVE_AUTH_MODE="user_token")
def test_drive_token_refresh_succeeds_no_exception():
    """Valid (or refreshable) token must not raise — and _get_token is called."""
    user = factories.UserFactory()
    workspace = factories.WorkspaceFactory()

    view = _make_view()
    data = _workspace_data(workspace, "drive")

    with patch(
        "core.api.views.workspaces_process.DriveUserTokenBackend"
    ) as mock_cls:
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

    with patch(
        "core.api.views.workspaces_process.DriveUserTokenBackend"
    ) as mock_cls:
        view.validation(user, data, [workspace])  # must not raise

    mock_cls.assert_not_called()


@override_settings(DRIVE_AUTH_MODE="user_token")
def test_no_drive_destination_skips_token_check():
    """Drive check is skipped when no workspace targets drive."""
    user = factories.UserFactory(oidc_access_token="", oidc_refresh_token="")
    workspace = factories.WorkspaceFactory()

    view = _make_view()
    data = _workspace_data(workspace, "resana")

    with patch(
        "core.api.views.workspaces_process.DriveUserTokenBackend"
    ) as mock_cls:
        with patch("core.api.views.workspaces_process.ResanaBackend") as mock_resana:
            mock_resana.return_value.fetch_user.return_value = {"id": "u1"}
            view.validation(user, data, [workspace])  # must not raise

    mock_cls.assert_not_called()
