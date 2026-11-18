"""Tests for ResanaBackend.refresh_job()'s completion email behavior."""

# pylint: disable=no-member  # django.core.mail.outbox is added by the locmem backend

from unittest.mock import patch

from django.core import mail

import pytest

from core import factories
from core.destinations.resana.resana_backend import ResanaBackend
from core.models import Workspace

pytestmark = pytest.mark.django_db

RESANA_URL = "https://resana.numerique.gouv.fr/public/"


def _workspace():
    user = factories.UserFactory(email="user@example.com")
    workspace = factories.WorkspaceFactory(title="Espace", migration_user=user)
    workspace.set_destination_metadata("resana", {"job_id": "job-1"})
    workspace.set_destination_status("resana", Workspace.Status.PENDING)
    workspace.save()
    return workspace


def test_refresh_job_sends_ready_mail_on_completed():
    """refresh_job() sends a 'resana_ready' migration mail when the job completed."""
    workspace = _workspace()

    with patch.object(
        ResanaBackend,
        "fetch_job",
        return_value={
            "status": "completed",
            "numberOfFilesSuccess": 10,
            "numberOfFilesError": 0,
        },
    ):
        ResanaBackend().refresh_job(workspace)

    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == [workspace.migration_user.email]
    assert workspace.title in sent.subject
    assert "Votre espace Resana est prêt" in sent.body
    assert RESANA_URL in sent.body
    assert "anti virus" not in sent.body


def test_refresh_job_sends_ready_errors_mail_on_failed():
    """refresh_job() sends a 'resana_ready_errors' migration mail when the job failed."""
    workspace = _workspace()

    with patch.object(
        ResanaBackend,
        "fetch_job",
        return_value={
            "status": "failed",
            "numberOfFilesSuccess": 0,
            "numberOfFilesError": 10,
        },
    ):
        ResanaBackend().refresh_job(workspace)

    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == [workspace.migration_user.email]
    assert workspace.title in sent.subject
    assert "anti virus" in sent.body
    assert RESANA_URL in sent.body


def test_refresh_job_sends_no_mail_while_job_still_running():
    """refresh_job() does not send any mail while the job is still in progress."""
    workspace = _workspace()

    with patch.object(
        ResanaBackend,
        "fetch_job",
        return_value={
            "status": "running",
            "numberOfFilesSuccess": 3,
            "numberOfFilesError": 0,
        },
    ):
        ResanaBackend().refresh_job(workspace)

    assert not mail.outbox
