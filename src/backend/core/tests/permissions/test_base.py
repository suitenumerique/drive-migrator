"""Tests for core/permissions/base.py — ABCs and PermissionApplicationResult."""

from unittest.mock import MagicMock

import pytest

from core.permissions.base import (
    DestinationPermissionWriter,
    PermissionApplicationResult,
    SourcePermissionReader,
)

# ---------------------------------------------------------------------------
# SourcePermissionReader
# ---------------------------------------------------------------------------


def test_source_reader_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        SourcePermissionReader()  # pylint: disable=abstract-class-instantiated


def test_source_reader_get_file_permission_default_returns_none():
    class ConcreteReader(SourcePermissionReader):
        def get_workspace_members(self, workspace_id):
            return []

    reader = ConcreteReader()
    assert reader.get_file_permission("any-id") is None


def test_source_reader_get_folder_permission_default_returns_none():
    class ConcreteReader(SourcePermissionReader):
        def get_workspace_members(self, workspace_id):
            return []

    reader = ConcreteReader()
    assert reader.get_folder_permission("any-id") is None


def test_source_reader_requires_get_workspace_members():
    class IncompleteReader(SourcePermissionReader):
        pass

    with pytest.raises(TypeError):
        IncompleteReader()  # pylint: disable=abstract-class-instantiated


# ---------------------------------------------------------------------------
# DestinationPermissionWriter
# ---------------------------------------------------------------------------


def test_destination_writer_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        DestinationPermissionWriter()  # pylint: disable=abstract-class-instantiated


def test_destination_writer_requires_apply_file_permission():
    class IncompleteWriter(DestinationPermissionWriter):
        pass

    with pytest.raises(TypeError):
        IncompleteWriter()  # pylint: disable=abstract-class-instantiated


def test_destination_writer_concrete_implementation():
    class ConcreteWriter(DestinationPermissionWriter):
        def apply_file_permission(
            self, dest_file_id, permission, resolved_users, policy
        ):
            return PermissionApplicationResult(success=True)

    writer = ConcreteWriter()
    result = writer.apply_file_permission("file-id", MagicMock(), {}, MagicMock())
    assert result.success is True


# ---------------------------------------------------------------------------
# PermissionApplicationResult
# ---------------------------------------------------------------------------


def test_permission_application_result_defaults():
    result = PermissionApplicationResult(success=True)
    assert result.success is True
    assert not result.skipped_users
    assert result.skipped_reason is None
    assert result.error is None


def test_permission_application_result_failure():
    result = PermissionApplicationResult(
        success=False,
        skipped_users=["ghost@example.com"],
        skipped_reason="no_equivalent_target",
        error=None,
    )
    assert result.success is False
    assert "ghost@example.com" in result.skipped_users
