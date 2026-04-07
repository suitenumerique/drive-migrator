"""Tests for ResanaDestinationBackend."""

from unittest.mock import MagicMock, patch

import pytest

from core.backends.destination import AbstractDestinationBackend
from core.destinations.resana.backend import ResanaDestinationBackend
from core.models import ResanaEmailMapping, Workspace


def test_implements_abstract_destination():
    """ResanaDestinationBackend must be a concrete AbstractDestinationBackend."""
    assert issubclass(ResanaDestinationBackend, AbstractDestinationBackend)


def test_name_is_resana():
    assert ResanaDestinationBackend.name == "resana"


def test_label_is_set():
    assert isinstance(ResanaDestinationBackend.label, str)
    assert ResanaDestinationBackend.label != ""


def test_export_calls_create_workspace(db):
    """export() delegates workspace creation to ResanaBackend."""
    workspace = MagicMock(spec=Workspace)
    user = MagicMock()

    with patch(
        "core.destinations.resana.backend.ResanaBackend"
    ) as mock_backend_cls:
        mock_backend = mock_backend_cls.return_value

        backend = ResanaDestinationBackend()
        backend.export(workspace, user, "/tmp/workspace")

    mock_backend.create_workspace.assert_called_once_with(workspace, user)


def test_export_sets_status_pending_for_async_job(db):
    """export() sets destination status to PENDING — Resana is an async job."""
    workspace = MagicMock(spec=Workspace)
    user = MagicMock()

    with patch("core.destinations.resana.backend.ResanaBackend"):
        backend = ResanaDestinationBackend()
        backend.export(workspace, user, "/tmp/workspace")

    workspace.set_destination_status.assert_called_once_with(
        "resana", Workspace.Status.PENDING
    )
    workspace.save.assert_called()


def test_get_error_details(db):
    """get_error_details() delegates to ResanaBackend.get_error_details()."""
    workspace = MagicMock(spec=Workspace)
    expected = [{"task": "failed"}]

    with patch(
        "core.destinations.resana.backend.ResanaBackend"
    ) as mock_backend_cls:
        mock_backend = mock_backend_cls.return_value
        mock_backend.get_error_details.return_value = expected

        backend = ResanaDestinationBackend()
        result = backend.get_error_details(workspace)

    mock_backend.get_error_details.assert_called_once_with(workspace)
    assert result == expected


def test_retry_job(db):
    """retry() delegates to ResanaBackend.retry_job()."""
    workspace = MagicMock(spec=Workspace)

    with patch(
        "core.destinations.resana.backend.ResanaBackend"
    ) as mock_backend_cls:
        mock_backend = mock_backend_cls.return_value

        backend = ResanaDestinationBackend()
        backend.retry(workspace)

    mock_backend.retry_job.assert_called_once_with(workspace)


@pytest.mark.django_db
def test_get_mapping_from_email_domain_match():
    """get_mapping_from_email() returns the mapping for an exact domain match."""
    from core.destinations.resana.resana_backend import ResanaBackend

    mapping = ResanaEmailMapping.objects.create(
        domain="example.com", resana_organization_uuid="uuid-org-1"
    )

    rb = ResanaBackend()
    result = rb.get_mapping_from_email("user@example.com")

    assert result == mapping


@pytest.mark.django_db
def test_get_mapping_from_email_wildcard_fallback():
    """get_mapping_from_email() falls back to the wildcard '*' mapping."""
    from core.destinations.resana.resana_backend import ResanaBackend

    wildcard = ResanaEmailMapping.objects.create(
        domain="*", resana_organization_uuid="uuid-default"
    )

    rb = ResanaBackend()
    result = rb.get_mapping_from_email("user@unknown.com")

    assert result == wildcard


@pytest.mark.django_db
def test_get_mapping_from_email_no_mapping_raises():
    """get_mapping_from_email() raises when no domain and no wildcard match."""
    from core.destinations.resana.resana_backend import ResanaBackend

    rb = ResanaBackend()
    with pytest.raises(Exception, match="No default mapping found"):
        rb.get_mapping_from_email("user@unknown.com")
