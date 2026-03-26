"""Tests for the generic Workspace model."""

import pytest

from core.models import Workspace


def test_workspace_defaults():
    """A new Workspace has empty JSONFields and NONE status."""
    workspace = Workspace()
    assert workspace.destination_statuses == {}
    assert workspace.destination_metadata == {}
    assert workspace.status == Workspace.Status.NONE


def test_get_destination_status_defaults_to_none():
    """get_destination_status returns NONE for an unknown destination."""
    workspace = Workspace()
    assert workspace.get_destination_status("archive") == Workspace.Status.NONE
    assert workspace.get_destination_status("drive") == Workspace.Status.NONE


def test_set_destination_status_updates_json():
    """set_destination_status writes to destination_statuses and syncs global status."""
    workspace = Workspace()
    workspace.set_destination_status("archive", Workspace.Status.SUCCESS)
    assert workspace.destination_statuses["archive"] == Workspace.Status.SUCCESS
    assert workspace.status == Workspace.Status.SUCCESS


def test_set_destination_status_syncs_global_status():
    """Global status reflects the worst state across all destinations."""
    workspace = Workspace()
    workspace.set_destination_status("archive", Workspace.Status.SUCCESS)
    workspace.set_destination_status("drive", Workspace.Status.FAILURE)
    assert workspace.status == Workspace.Status.FAILURE


def test_destination_metadata_read_write():
    """get/set_destination_metadata stores arbitrary dicts per destination."""
    workspace = Workspace()
    workspace.set_destination_metadata("resana", {"id": "uuid-123", "job_id": "job-456"})
    assert workspace.get_destination_metadata("resana") == {
        "id": "uuid-123",
        "job_id": "job-456",
    }


def test_get_destination_metadata_defaults_to_empty_dict():
    """get_destination_metadata returns {} for an unknown destination."""
    workspace = Workspace()
    assert workspace.get_destination_metadata("unknown") == {}


def test_compute_status_no_destinations():
    """compute_status returns NONE when no destination statuses are set."""
    workspace = Workspace()
    assert workspace.compute_status() == Workspace.Status.NONE


def test_compute_status_all_none():
    """compute_status returns NONE when all destinations are NONE."""
    workspace = Workspace()
    workspace.destination_statuses = {"archive": "NONE", "drive": "NONE"}
    assert workspace.compute_status() == Workspace.Status.NONE


def test_compute_status_pending_wins():
    """PENDING takes precedence over FAILURE and SUCCESS."""
    workspace = Workspace()
    workspace.destination_statuses = {
        "archive": Workspace.Status.SUCCESS,
        "drive": Workspace.Status.PENDING,
        "resana": Workspace.Status.FAILURE,
    }
    assert workspace.compute_status() == Workspace.Status.PENDING


def test_compute_status_failure_over_success():
    """FAILURE takes precedence over SUCCESS."""
    workspace = Workspace()
    workspace.destination_statuses = {
        "archive": Workspace.Status.SUCCESS,
        "drive": Workspace.Status.FAILURE,
    }
    assert workspace.compute_status() == Workspace.Status.FAILURE


def test_compute_status_all_success():
    """compute_status returns SUCCESS when all destinations succeeded."""
    workspace = Workspace()
    workspace.destination_statuses = {
        "archive": Workspace.Status.SUCCESS,
        "drive": Workspace.Status.SUCCESS,
    }
    assert workspace.compute_status() == Workspace.Status.SUCCESS


def test_compute_status_single_destination_all_combinations():
    """
    Table of truth for a single destination — mirrors the original 16-combination test
    using the new generic API.
    """
    workspace = Workspace()

    # (dest_a, dest_b, expected_global)
    combinations = [
        (Workspace.Status.NONE, Workspace.Status.NONE, Workspace.Status.NONE),
        (Workspace.Status.NONE, Workspace.Status.PENDING, Workspace.Status.PENDING),
        (Workspace.Status.NONE, Workspace.Status.FAILURE, Workspace.Status.FAILURE),
        (Workspace.Status.NONE, Workspace.Status.SUCCESS, Workspace.Status.SUCCESS),
        (Workspace.Status.PENDING, Workspace.Status.NONE, Workspace.Status.PENDING),
        (Workspace.Status.PENDING, Workspace.Status.PENDING, Workspace.Status.PENDING),
        (Workspace.Status.PENDING, Workspace.Status.FAILURE, Workspace.Status.PENDING),
        (Workspace.Status.PENDING, Workspace.Status.SUCCESS, Workspace.Status.PENDING),
        (Workspace.Status.FAILURE, Workspace.Status.NONE, Workspace.Status.FAILURE),
        (Workspace.Status.FAILURE, Workspace.Status.PENDING, Workspace.Status.PENDING),
        (Workspace.Status.FAILURE, Workspace.Status.FAILURE, Workspace.Status.FAILURE),
        (Workspace.Status.FAILURE, Workspace.Status.SUCCESS, Workspace.Status.FAILURE),
        (Workspace.Status.SUCCESS, Workspace.Status.NONE, Workspace.Status.SUCCESS),
        (Workspace.Status.SUCCESS, Workspace.Status.PENDING, Workspace.Status.PENDING),
        (Workspace.Status.SUCCESS, Workspace.Status.FAILURE, Workspace.Status.FAILURE),
        (Workspace.Status.SUCCESS, Workspace.Status.SUCCESS, Workspace.Status.SUCCESS),
    ]

    for dest_a, dest_b, expected in combinations:
        workspace.set_destination_status("dest_a", dest_a)
        workspace.set_destination_status("dest_b", dest_b)
        assert workspace.status == expected, (
            f"dest_a={dest_a}, dest_b={dest_b} → expected {expected}, got {workspace.status}"
        )
