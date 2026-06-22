"""Tests for DrivePermissionWriter."""

from unittest.mock import MagicMock

import requests

from core.destinations.drive.drive_permission_writer import DrivePermissionWriter
from core.permissions.models import (
    CanonicalRole,
    NormalizedFilePermission,
    PermissionTarget,
    UserPermission,
)
from core.permissions.policy import OnUnresolved, PermissionMappingPolicy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_writer():
    backend = MagicMock()
    backend.find_user_by_email.return_value = None
    return DrivePermissionWriter(backend), backend


def _permission(target, users=None, groups=None):
    return NormalizedFilePermission(
        target=target,
        user_permissions=users or [],
        groups=groups or [],
    )


def _policy(**kwargs):
    return PermissionMappingPolicy(**kwargs)


# ---------------------------------------------------------------------------
# Group 1 — Targets that require no action (PRIVATE, ALL_MEMBERS)
# ---------------------------------------------------------------------------


def test_apply_private_returns_success():
    """PRIVATE permission requires no Drive sharing — writer succeeds without any API call."""
    writer, backend = _make_writer()

    result = writer.apply_file_permission(
        "file-id",
        _permission(PermissionTarget.PRIVATE),
        {},
        _policy(),
    )

    assert result.success is True
    backend.share_with_user.assert_not_called()
    backend.invite_by_email.assert_not_called()


def test_apply_private_sets_skipped_reason():
    """PRIVATE result carries a non-empty skipped_reason so callers can log it."""
    writer, _ = _make_writer()

    result = writer.apply_file_permission(
        "file-id",
        _permission(PermissionTarget.PRIVATE),
        {},
        _policy(),
    )

    assert result.skipped_reason is not None


def test_apply_all_members_returns_success():
    """ALL_MEMBERS is handled at workspace level — no per-file action needed."""
    writer, backend = _make_writer()

    result = writer.apply_file_permission(
        "file-id",
        _permission(PermissionTarget.ALL_MEMBERS),
        {},
        _policy(),
    )

    assert result.success is True
    backend.share_with_user.assert_not_called()
    backend.invite_by_email.assert_not_called()


def test_apply_all_members_sets_skipped_reason():
    """ALL_MEMBERS result carries a non-empty skipped_reason."""
    writer, _ = _make_writer()

    result = writer.apply_file_permission(
        "file-id",
        _permission(PermissionTarget.ALL_MEMBERS),
        {},
        _policy(),
    )

    assert result.skipped_reason is not None


# ---------------------------------------------------------------------------
# Group 2 — Targets without a Drive equivalent (MANAGERS_CONTRIBUTORS, RESTRICTED_GROUPS)
# ---------------------------------------------------------------------------


def test_apply_managers_contributors_returns_not_success_by_default():
    """MANAGERS_CONTRIBUTORS has no Drive equivalent — default WARN policy marks it skipped."""
    writer, _ = _make_writer()

    result = writer.apply_file_permission(
        "file-id",
        _permission(PermissionTarget.MANAGERS_CONTRIBUTORS),
        {},
        _policy(),
    )

    assert result.success is False
    assert result.skipped_reason is not None


def test_apply_restricted_groups_returns_not_success_by_default():
    """RESTRICTED_GROUPS has no Drive equivalent — default WARN policy marks it skipped."""
    writer, _ = _make_writer()

    result = writer.apply_file_permission(
        "file-id",
        _permission(PermissionTarget.RESTRICTED_GROUPS),
        {},
        _policy(),
    )

    assert result.success is False
    assert result.skipped_reason is not None


def test_apply_no_equivalent_target_with_fail_policy_sets_error():
    """on_no_equivalent_target=FAIL returns a failure result with error and no skipped_reason."""
    writer, _ = _make_writer()

    result = writer.apply_file_permission(
        "file-id",
        _permission(PermissionTarget.MANAGERS_CONTRIBUTORS),
        {},
        _policy(on_no_equivalent_target=OnUnresolved.FAIL),
    )

    assert result.success is False
    assert result.error is not None
    assert result.skipped_reason is None


# ---------------------------------------------------------------------------
# Group 3 — SPECIFIC_USERS: role mapping
# ---------------------------------------------------------------------------


def test_apply_specific_users_maps_read_to_reader():
    """CanonicalRole.READ is sent to Drive as role='reader'."""
    writer, backend = _make_writer()
    backend.find_user_by_email.return_value = {"id": "drive-user-1"}
    permission = _permission(
        PermissionTarget.SPECIFIC_USERS,
        users=[UserPermission(email="alice@example.com", role=CanonicalRole.READ)],
    )

    writer.apply_file_permission(
        "file-id", permission, {"alice@example.com": "alice@example.com"}, _policy()
    )

    backend.share_with_user.assert_called_once_with("file-id", "drive-user-1", "reader")


def test_apply_specific_users_maps_write_to_editor():
    """CanonicalRole.WRITE is sent to Drive as role='editor'."""
    writer, backend = _make_writer()
    backend.find_user_by_email.return_value = {"id": "drive-user-2"}
    permission = _permission(
        PermissionTarget.SPECIFIC_USERS,
        users=[UserPermission(email="bob@example.com", role=CanonicalRole.WRITE)],
    )

    writer.apply_file_permission(
        "file-id", permission, {"bob@example.com": "bob@example.com"}, _policy()
    )

    backend.share_with_user.assert_called_once_with("file-id", "drive-user-2", "editor")


def test_apply_specific_users_maps_manage_to_owner():
    """CanonicalRole.MANAGE is sent to Drive as role='owner'."""
    writer, backend = _make_writer()
    backend.find_user_by_email.return_value = {"id": "drive-user-3"}
    permission = _permission(
        PermissionTarget.SPECIFIC_USERS,
        users=[UserPermission(email="carol@example.com", role=CanonicalRole.MANAGE)],
    )

    writer.apply_file_permission(
        "file-id", permission, {"carol@example.com": "carol@example.com"}, _policy()
    )

    backend.share_with_user.assert_called_once_with("file-id", "drive-user-3", "owner")


# ---------------------------------------------------------------------------
# Group 4 — SPECIFIC_USERS: share vs invite
# ---------------------------------------------------------------------------


def test_apply_specific_users_shares_when_drive_user_found():
    """When find_user_by_email returns a user, share_with_user is called (not invite_by_email)."""
    writer, backend = _make_writer()
    backend.find_user_by_email.return_value = {"id": "drive-uid"}
    permission = _permission(
        PermissionTarget.SPECIFIC_USERS,
        users=[UserPermission(email="known@example.com", role=CanonicalRole.READ)],
    )

    result = writer.apply_file_permission(
        "file-id", permission, {"known@example.com": "known@example.com"}, _policy()
    )

    assert result.success is True
    backend.share_with_user.assert_called_once_with("file-id", "drive-uid", "reader")
    backend.invite_by_email.assert_not_called()


def test_apply_specific_users_invites_when_drive_user_not_found():
    """When find_user_by_email returns None, invite_by_email is called (not share_with_user)."""
    writer, backend = _make_writer()
    backend.find_user_by_email.return_value = None
    permission = _permission(
        PermissionTarget.SPECIFIC_USERS,
        users=[UserPermission(email="new@example.com", role=CanonicalRole.READ)],
    )

    result = writer.apply_file_permission(
        "file-id", permission, {"new@example.com": "new@example.com"}, _policy()
    )

    assert result.success is True
    backend.invite_by_email.assert_called_once_with(
        "file-id", "new@example.com", "reader"
    )
    backend.share_with_user.assert_not_called()


def test_apply_specific_users_uses_resolved_dest_email():
    """The writer uses the resolved destination email (not the source email) to look up the Drive user."""
    writer, backend = _make_writer()
    backend.find_user_by_email.return_value = None
    permission = _permission(
        PermissionTarget.SPECIFIC_USERS,
        users=[UserPermission(email="source@old.com", role=CanonicalRole.READ)],
    )

    writer.apply_file_permission(
        "file-id", permission, {"source@old.com": "dest@new.com"}, _policy()
    )

    backend.find_user_by_email.assert_called_once_with("dest@new.com")


def test_apply_specific_users_processes_multiple_users():
    """All resolved users in the permission are shared or invited."""
    writer, backend = _make_writer()
    backend.find_user_by_email.return_value = None
    permission = _permission(
        PermissionTarget.SPECIFIC_USERS,
        users=[
            UserPermission(email="alice@example.com", role=CanonicalRole.READ),
            UserPermission(email="bob@example.com", role=CanonicalRole.WRITE),
        ],
    )
    resolved = {
        "alice@example.com": "alice@example.com",
        "bob@example.com": "bob@example.com",
    }

    result = writer.apply_file_permission("file-id", permission, resolved, _policy())

    assert result.success is True
    assert backend.invite_by_email.call_count == 2


# ---------------------------------------------------------------------------
# Group 5 — SPECIFIC_USERS: unresolved users
# ---------------------------------------------------------------------------


def test_apply_specific_users_skips_unresolved_user():
    """A user absent from resolved_users is recorded in skipped_users, result is still success."""
    writer, _ = _make_writer()
    permission = _permission(
        PermissionTarget.SPECIFIC_USERS,
        users=[
            UserPermission(email="known@example.com", role=CanonicalRole.READ),
            UserPermission(email="unknown@example.com", role=CanonicalRole.READ),
        ],
    )
    resolved = {"known@example.com": "known@example.com"}

    result = writer.apply_file_permission("file-id", permission, resolved, _policy())

    assert result.success is True
    assert "unknown@example.com" in result.skipped_users


def test_apply_specific_users_does_not_call_backend_for_unresolved():
    """No Drive API call is made for users absent from resolved_users."""
    writer, backend = _make_writer()
    permission = _permission(
        PermissionTarget.SPECIFIC_USERS,
        users=[UserPermission(email="ghost@example.com", role=CanonicalRole.READ)],
    )

    writer.apply_file_permission("file-id", permission, {}, _policy())

    backend.find_user_by_email.assert_not_called()
    backend.share_with_user.assert_not_called()
    backend.invite_by_email.assert_not_called()


def test_apply_specific_users_fails_when_policy_fail_on_unresolved():
    """on_unresolved_user=FAIL returns a failure result with error when a user cannot be resolved."""
    writer, _ = _make_writer()
    permission = _permission(
        PermissionTarget.SPECIFIC_USERS,
        users=[UserPermission(email="ghost@example.com", role=CanonicalRole.READ)],
    )

    result = writer.apply_file_permission(
        "file-id",
        permission,
        {},
        _policy(on_unresolved_user=OnUnresolved.FAIL),
    )

    assert result.success is False
    assert result.error is not None


# ---------------------------------------------------------------------------
# Group 6 — Error handling
# ---------------------------------------------------------------------------


def test_apply_returns_failure_on_http_error():
    """An HTTPError from the Drive API is caught and returned as a failure result."""
    writer, backend = _make_writer()
    backend.find_user_by_email.return_value = None
    backend.invite_by_email.side_effect = requests.HTTPError("403 Forbidden")
    permission = _permission(
        PermissionTarget.SPECIFIC_USERS,
        users=[UserPermission(email="user@example.com", role=CanonicalRole.READ)],
    )

    result = writer.apply_file_permission(
        "file-id", permission, {"user@example.com": "user@example.com"}, _policy()
    )

    assert result.success is False
    assert result.error is not None
