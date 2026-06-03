"""Maps source user emails to destination user emails."""

from __future__ import annotations

from core.permissions.models import NormalizedFilePermission


class UserResolver:
    """Maps source user emails to destination user emails for a given workspace."""

    def __init__(self, workspace):
        self.workspace = workspace

    def resolve(self, source_email: str) -> str | None:
        """Return the destination email for a source email, or None if unresolvable."""
        # Future: lookup a UserEmailMapping table here
        return source_email

    def resolve_all(self, permission: NormalizedFilePermission) -> dict[str, str]:
        """Return {source_email: dest_email} for all resolvable users in the permission."""
        resolved = {}
        for user_permission in permission.user_permissions:
            dest_email = self.resolve(user_permission.email)
            if dest_email is not None:
                resolved[user_permission.email] = dest_email
        return resolved
