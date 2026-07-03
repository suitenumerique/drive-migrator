"""Tests for ResanaBackend.refresh_job()'s completion email behavior."""

from unittest.mock import MagicMock, patch

from core.destinations.resana.resana_backend import ResanaBackend
from core.models import Workspace


def _make_workspace(job_id="job-1", status=Workspace.Status.PENDING):
    workspace = MagicMock()
    workspace.get_destination_metadata.return_value = {"job_id": job_id}
    workspace.get_destination_status.return_value = status
    workspace.migration_user = MagicMock()
    return workspace


def test_refresh_job_sends_ready_mail_on_completed():
    """refresh_job() sends a 'resana_ready' migration mail when the job completed."""
    workspace = _make_workspace()

    with (
        patch.object(
            ResanaBackend,
            "fetch_job",
            return_value={
                "status": "completed",
                "numberOfFilesSuccess": 10,
                "numberOfFilesError": 0,
            },
        ),
        patch("core.destinations.resana.resana_backend.MailsManager") as mock_mails_cls,
    ):
        ResanaBackend().refresh_job(workspace)

    mock_send = mock_mails_cls.return_value.send_migration_mail
    mock_send.assert_called_once()
    args, kwargs = mock_send.call_args
    assert args[:3] == (workspace.migration_user, workspace, "resana_ready")
    assert str(args[3]["title"])
    assert args[3]["url"] == "https://resana.numerique.gouv.fr/public/"
    assert kwargs == {}


def test_refresh_job_sends_ready_errors_mail_on_failed():
    """refresh_job() sends a 'resana_ready_errors' migration mail when the job failed."""
    workspace = _make_workspace()

    with (
        patch.object(
            ResanaBackend,
            "fetch_job",
            return_value={
                "status": "failed",
                "numberOfFilesSuccess": 0,
                "numberOfFilesError": 10,
            },
        ),
        patch("core.destinations.resana.resana_backend.MailsManager") as mock_mails_cls,
    ):
        ResanaBackend().refresh_job(workspace)

    mock_send = mock_mails_cls.return_value.send_migration_mail
    mock_send.assert_called_once()
    args, kwargs = mock_send.call_args
    assert args[:3] == (
        workspace.migration_user,
        workspace,
        "resana_ready_errors",
    )
    assert str(args[3]["title"])
    assert args[3]["url"] == "https://resana.numerique.gouv.fr/public/"
    assert kwargs == {}


def test_refresh_job_sends_no_mail_while_job_still_running():
    """refresh_job() does not send any mail while the job is still in progress."""
    workspace = _make_workspace()

    with (
        patch.object(
            ResanaBackend,
            "fetch_job",
            return_value={
                "status": "running",
                "numberOfFilesSuccess": 3,
                "numberOfFilesError": 0,
            },
        ),
        patch("core.destinations.resana.resana_backend.MailsManager") as mock_mails_cls,
    ):
        ResanaBackend().refresh_job(workspace)

    mock_mails_cls.return_value.send_migration_mail.assert_not_called()
