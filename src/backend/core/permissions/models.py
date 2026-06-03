"""Canonical permission model — source/destination-agnostic."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CanonicalRole(str, Enum):
    READ = "read"
    WRITE = "write"
    MANAGE = "manage"


class PermissionTarget(str, Enum):
    ALL_MEMBERS = "all_members"
    MANAGERS_CONTRIBUTORS = "managers_contributors"
    SPECIFIC_USERS = "specific_users"
    RESTRICTED_GROUPS = "restricted_groups"
    PRIVATE = "private"


@dataclass
class UserPermission:
    email: str
    role: CanonicalRole

    def to_dict(self) -> dict:
        return {"email": self.email, "role": self.role.value}

    @classmethod
    def from_dict(cls, data: dict) -> "UserPermission":
        return cls(email=data["email"], role=CanonicalRole(data["role"]))


@dataclass
class NormalizedFilePermission:
    target: PermissionTarget
    user_permissions: list[UserPermission] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "target": self.target.value,
            "user_permissions": [up.to_dict() for up in self.user_permissions],
            "groups": list(self.groups),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NormalizedFilePermission":
        return cls(
            target=PermissionTarget(data["target"]),
            user_permissions=[
                UserPermission.from_dict(up) for up in data.get("user_permissions", [])
            ],
            groups=list(data.get("groups", [])),
        )
