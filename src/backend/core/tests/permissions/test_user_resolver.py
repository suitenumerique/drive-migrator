"""Tests for core/permissions/user_resolver.py."""

from unittest.mock import MagicMock

from core.permissions.models import (
    CanonicalRole,
    NormalizedFilePermission,
    PermissionTarget,
    UserPermission,
)
from core.permissions.user_resolver import UserResolver


def _make_workspace():
    return MagicMock()


def test_resolve_returns_same_email():
    resolver = UserResolver(_make_workspace())
    assert resolver.resolve("user@example.com") == "user@example.com"


def test_resolve_all_returns_identity_mapping():
    perm = NormalizedFilePermission(
        target=PermissionTarget.SPECIFIC_USERS,
        user_permissions=[
            UserPermission(email="a@example.com", role=CanonicalRole.READ),
            UserPermission(email="b@example.com", role=CanonicalRole.WRITE),
        ],
    )
    resolver = UserResolver(_make_workspace())
    result = resolver.resolve_all(perm)
    assert result == {
        "a@example.com": "a@example.com",
        "b@example.com": "b@example.com",
    }


def test_resolve_all_empty_when_no_user_permissions():
    perm = NormalizedFilePermission(target=PermissionTarget.ALL_MEMBERS)
    resolver = UserResolver(_make_workspace())
    assert not resolver.resolve_all(perm)
