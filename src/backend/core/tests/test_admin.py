"""Tests for the UserAdmin token-reset actions."""

from django.contrib import admin
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from django.utils import timezone

import pytest

from core.admin import UserAdmin
from core.factories import UserFactory
from core.models import User

pytestmark = pytest.mark.django_db


def _admin_request():
    """Build a fake admin POST request with a working messages framework."""
    request = RequestFactory().post("/admin/core/user/")
    request.session = {}
    request._messages = FallbackStorage(request)  # pylint: disable=protected-access
    return request


def _connected_user():
    return UserFactory(
        resana_access_token="resana-access",
        resana_refresh_token="resana-refresh",
        resana_session_id="session-id",
        resana_csrf_token="csrf-token",
        resana_token_expires_at=timezone.now(),
        oidc_access_token="drive-access",
        oidc_refresh_token="drive-refresh",
        oidc_token_expires_at=timezone.now(),
    )


def test_reset_resana_connection_clears_resana_tokens_only():
    """reset_resana_connection clears Resana tokens and leaves Drive tokens untouched."""
    user = _connected_user()
    previous_updated_at = user.updated_at
    user_admin = UserAdmin(User, admin.site)

    user_admin.reset_resana_connection(
        _admin_request(), User.objects.filter(pk=user.pk)
    )

    user.refresh_from_db()
    assert user.resana_access_token == ""
    assert user.resana_refresh_token == ""
    assert user.resana_session_id == ""
    assert user.resana_csrf_token == ""
    assert user.resana_token_expires_at is None
    assert user.oidc_access_token == "drive-access"
    assert user.oidc_refresh_token == "drive-refresh"
    assert user.oidc_token_expires_at is not None
    assert user.updated_at > previous_updated_at


def test_reset_drive_connection_clears_drive_tokens_only():
    """reset_drive_connection clears Drive tokens and leaves Resana tokens untouched."""
    user = _connected_user()
    previous_updated_at = user.updated_at
    user_admin = UserAdmin(User, admin.site)

    user_admin.reset_drive_connection(_admin_request(), User.objects.filter(pk=user.pk))

    user.refresh_from_db()
    assert user.oidc_access_token == ""
    assert user.oidc_refresh_token == ""
    assert user.oidc_token_expires_at is None
    assert user.resana_access_token == "resana-access"
    assert user.resana_refresh_token == "resana-refresh"
    assert user.resana_token_expires_at is not None
    assert user.updated_at > previous_updated_at


def test_reset_resana_connection_applies_to_every_selected_user():
    """The action iterates over the whole queryset, not just the first user."""
    users = [_connected_user(), _connected_user()]
    user_admin = UserAdmin(User, admin.site)

    user_admin.reset_resana_connection(
        _admin_request(), User.objects.filter(pk__in=[u.pk for u in users])
    )

    for user in users:
        user.refresh_from_db()
        assert user.resana_access_token == ""
