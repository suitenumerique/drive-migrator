"""DrivePermissionWriter — applies normalized file permissions to La Suite Drive."""

import requests

from core.permissions.base import (
    DestinationPermissionWriter,
    PermissionApplicationResult,
)
from core.permissions.models import (
    CanonicalRole,
    NormalizedFilePermission,
    PermissionTarget,
)
from core.permissions.policy import OnUnresolved, PermissionMappingPolicy

_ROLE_MAP: dict[CanonicalRole, str] = {
    CanonicalRole.READ: "reader",
    CanonicalRole.WRITE: "editor",
    CanonicalRole.MANAGE: "owner",
}

_TARGETS_WITHOUT_DRIVE_EQUIVALENT = {
    PermissionTarget.MANAGERS_CONTRIBUTORS,
    PermissionTarget.RESTRICTED_GROUPS,
}


class DrivePermissionWriter(DestinationPermissionWriter):
    """Applies a NormalizedFilePermission to a Drive item.

    Targets PRIVATE and ALL_MEMBERS require no per-file action and return success
    immediately. Targets without a Drive equivalent (MANAGERS_CONTRIBUTORS,
    RESTRICTED_GROUPS) follow policy.on_no_equivalent_target. SPECIFIC_USERS
    resolves each user via the backend and applies the mapped Drive role.
    """

    def __init__(self, backend):
        self._backend = backend

    def apply_file_permission(
        self,
        dest_file_id: str,
        permission: NormalizedFilePermission,
        resolved_users: dict[str, str],
        policy: PermissionMappingPolicy,
    ) -> PermissionApplicationResult:
        """Apply the normalized permission to the Drive item identified by dest_file_id."""
        if permission.target == PermissionTarget.PRIVATE:
            return PermissionApplicationResult(
                success=True,
                skipped_reason="private: no sharing required",
            )

        if permission.target == PermissionTarget.ALL_MEMBERS:
            return PermissionApplicationResult(
                success=True,
                skipped_reason="all_members: workspace-level sharing handles this",
            )

        if permission.target in _TARGETS_WITHOUT_DRIVE_EQUIVALENT:
            return self._handle_no_equivalent(permission.target, policy)

        return self._apply_specific_users(
            dest_file_id, permission, resolved_users, policy
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _handle_no_equivalent(
        self, target: PermissionTarget, policy: PermissionMappingPolicy
    ) -> PermissionApplicationResult:
        reason = f"no Drive equivalent for target: {target.value}"
        if policy.on_no_equivalent_target == OnUnresolved.FAIL:
            return PermissionApplicationResult(success=False, error=reason)
        return PermissionApplicationResult(success=False, skipped_reason=reason)

    def _apply_specific_users(
        self,
        dest_file_id: str,
        permission: NormalizedFilePermission,
        resolved_users: dict[str, str],
        policy: PermissionMappingPolicy,
    ) -> PermissionApplicationResult:
        skipped_users: list[str] = []

        for user_perm in permission.user_permissions:
            dest_email = resolved_users.get(user_perm.email)
            if dest_email is None:
                if policy.on_unresolved_user == OnUnresolved.FAIL:
                    return PermissionApplicationResult(
                        success=False,
                        error=f"unresolved user: {user_perm.email}",
                    )
                skipped_users.append(user_perm.email)
                continue

            drive_role = _ROLE_MAP[user_perm.role]
            try:
                self._share_with(dest_file_id, dest_email, drive_role)
            except requests.HTTPError as exc:
                return PermissionApplicationResult(success=False, error=str(exc))

        return PermissionApplicationResult(success=True, skipped_users=skipped_users)

    def _share_with(self, item_id: str, email: str, drive_role: str) -> None:
        drive_user = self._backend.find_user_by_email(email)
        if drive_user:
            self._backend.share_with_user(item_id, drive_user["id"], drive_role)
        else:
            self._backend.invite_by_email(item_id, email, drive_role)
