"""Abstract interfaces for permission readers and writers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from core.permissions.models import NormalizedFilePermission, UserPermission
from core.permissions.policy import PermissionMappingPolicy


class SourcePermissionReader(ABC):
    """Implemented by each source backend that supports permission extraction."""

    @abstractmethod
    def get_workspace_members(self, workspace_id: str) -> list[UserPermission]:
        """Return all workspace members with their workspace-level role."""

    def get_file_permission(self, _file_id: str) -> NormalizedFilePermission | None:
        """Return normalized permission for a single file, or None if unsupported."""
        return None

    def get_folder_permission(self, _folder_id: str) -> NormalizedFilePermission | None:
        """Return normalized permission for a folder, or None if unsupported."""
        return None


@dataclass
class PermissionApplicationResult:
    """Outcome of a single file permission application attempt."""

    success: bool
    skipped_users: list[str] = field(default_factory=list)
    skipped_reason: str | None = None
    error: str | None = None


class DestinationPermissionWriter(ABC):
    """Implemented by each destination backend that supports permission application."""

    @abstractmethod
    def apply_file_permission(
        self,
        dest_file_id: str,
        permission: NormalizedFilePermission,
        resolved_users: dict[str, str],
        policy: PermissionMappingPolicy,
    ) -> PermissionApplicationResult:
        """Apply normalized permission to a destination file."""
