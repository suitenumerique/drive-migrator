"""Tests for core/permissions/models.py."""

import pytest

from core.permissions.models import (
    CanonicalRole,
    NormalizedFilePermission,
    PermissionTarget,
    UserPermission,
)

# ---------------------------------------------------------------------------
# CanonicalRole
# ---------------------------------------------------------------------------


def test_canonical_role_values():
    assert CanonicalRole.READ == "read"
    assert CanonicalRole.WRITE == "write"
    assert CanonicalRole.MANAGE == "manage"


# ---------------------------------------------------------------------------
# PermissionTarget
# ---------------------------------------------------------------------------


def test_permission_target_values():
    assert PermissionTarget.ALL_MEMBERS == "all_members"
    assert PermissionTarget.MANAGERS_CONTRIBUTORS == "managers_contributors"
    assert PermissionTarget.SPECIFIC_USERS == "specific_users"
    assert PermissionTarget.RESTRICTED_GROUPS == "restricted_groups"
    assert PermissionTarget.PRIVATE == "private"


# ---------------------------------------------------------------------------
# UserPermission
# ---------------------------------------------------------------------------


def test_user_permission_to_dict():
    up = UserPermission(email="user@example.com", role=CanonicalRole.READ)
    assert up.to_dict() == {"email": "user@example.com", "role": "read"}


def test_user_permission_from_dict():
    up = UserPermission.from_dict({"email": "user@example.com", "role": "write"})
    assert up.email == "user@example.com"
    assert up.role == CanonicalRole.WRITE


def test_user_permission_round_trip():
    original = UserPermission(email="admin@example.com", role=CanonicalRole.MANAGE)
    assert UserPermission.from_dict(original.to_dict()) == original


# ---------------------------------------------------------------------------
# NormalizedFilePermission — to_dict / from_dict
# ---------------------------------------------------------------------------


def test_normalized_permission_all_members_to_dict():
    perm = NormalizedFilePermission(target=PermissionTarget.ALL_MEMBERS)
    d = perm.to_dict()
    assert d["target"] == "all_members"
    assert not d["user_permissions"]
    assert not d["groups"]


def test_normalized_permission_private_to_dict():
    perm = NormalizedFilePermission(target=PermissionTarget.PRIVATE)
    assert perm.to_dict()["target"] == "private"


def test_normalized_permission_specific_users_to_dict():
    perm = NormalizedFilePermission(
        target=PermissionTarget.SPECIFIC_USERS,
        user_permissions=[
            UserPermission(email="a@example.com", role=CanonicalRole.READ),
            UserPermission(email="b@example.com", role=CanonicalRole.MANAGE),
        ],
    )
    d = perm.to_dict()
    assert d["target"] == "specific_users"
    assert len(d["user_permissions"]) == 2
    assert d["user_permissions"][0] == {"email": "a@example.com", "role": "read"}


def test_normalized_permission_restricted_groups_to_dict():
    perm = NormalizedFilePermission(
        target=PermissionTarget.RESTRICTED_GROUPS,
        groups=["AGENT", "PARTENAIRE"],
    )
    d = perm.to_dict()
    assert d["target"] == "restricted_groups"
    assert d["groups"] == ["AGENT", "PARTENAIRE"]


def test_normalized_permission_from_dict_all_members():
    perm = NormalizedFilePermission.from_dict(
        {"target": "all_members", "user_permissions": [], "groups": []}
    )
    assert perm.target == PermissionTarget.ALL_MEMBERS
    assert not perm.user_permissions
    assert not perm.groups


def test_normalized_permission_from_dict_specific_users():
    perm = NormalizedFilePermission.from_dict(
        {
            "target": "specific_users",
            "user_permissions": [{"email": "x@example.com", "role": "write"}],
            "groups": [],
        }
    )
    assert perm.target == PermissionTarget.SPECIFIC_USERS
    assert len(perm.user_permissions) == 1
    assert perm.user_permissions[0].email == "x@example.com"
    assert perm.user_permissions[0].role == CanonicalRole.WRITE


@pytest.mark.parametrize(
    "target",
    [
        PermissionTarget.ALL_MEMBERS,
        PermissionTarget.MANAGERS_CONTRIBUTORS,
        PermissionTarget.SPECIFIC_USERS,
        PermissionTarget.RESTRICTED_GROUPS,
        PermissionTarget.PRIVATE,
    ],
)
def test_normalized_permission_round_trip(target):
    perm = NormalizedFilePermission(
        target=target,
        user_permissions=[
            UserPermission(email="u@example.com", role=CanonicalRole.READ)
        ]
        if target == PermissionTarget.SPECIFIC_USERS
        else [],
        groups=["AGENT"] if target == PermissionTarget.RESTRICTED_GROUPS else [],
    )
    assert NormalizedFilePermission.from_dict(perm.to_dict()) == perm
