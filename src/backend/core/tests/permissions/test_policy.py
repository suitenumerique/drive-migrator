"""Tests for core/permissions/policy.py."""

from core.permissions.models import PermissionTarget
from core.permissions.policy import OnUnresolved, PermissionMappingPolicy


def test_on_unresolved_values():
    assert OnUnresolved.SKIP == "skip"
    assert OnUnresolved.WARN == "warn"
    assert OnUnresolved.FAIL == "fail"


def test_policy_default_values():
    policy = PermissionMappingPolicy()
    assert policy.on_unresolved_user == OnUnresolved.WARN
    assert policy.on_no_equivalent_target == OnUnresolved.WARN
    assert policy.fallback_target is None


def test_policy_custom_values():
    policy = PermissionMappingPolicy(
        on_unresolved_user=OnUnresolved.SKIP,
        on_no_equivalent_target=OnUnresolved.FAIL,
        fallback_target=PermissionTarget.ALL_MEMBERS,
    )
    assert policy.on_unresolved_user == OnUnresolved.SKIP
    assert policy.on_no_equivalent_target == OnUnresolved.FAIL
    assert policy.fallback_target == PermissionTarget.ALL_MEMBERS
