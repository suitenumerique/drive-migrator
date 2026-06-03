"""Permission mapping policy — controls behavior for unresolvable cases."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.permissions.models import PermissionTarget


class OnUnresolved(str, Enum):
    """Action to take when a permission element cannot be mapped to the destination."""

    SKIP = "skip"
    WARN = "warn"
    FAIL = "fail"


@dataclass
class PermissionMappingPolicy:
    """Controls how unresolvable users and targets are handled during permission migration."""

    on_unresolved_user: OnUnresolved = OnUnresolved.WARN
    on_no_equivalent_target: OnUnresolved = OnUnresolved.WARN
    fallback_target: PermissionTarget | None = None
