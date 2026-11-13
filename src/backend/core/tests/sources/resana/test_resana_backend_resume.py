"""Tests for ResanaSourceBackend.resume_export(), for the tasks of a run
other than its first one."""

from unittest.mock import MagicMock, patch

from core.backends.source import SourceFile
from core.sources.resana.backend import ResanaSourceBackend


def test_resume_export_lets_a_new_backend_download_for_the_migration_user(settings):
    """A file task builds its own backend: resume_export() gives it the user
    begin_export() set in the run's first task."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    workspace = MagicMock()
    backend = ResanaSourceBackend()
    source_file = SourceFile(
        id="file-uuid", name="doc", extension=".pdf", download_url="file-uuid"
    )

    with (
        patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm,
        patch("core.sources.resana.backend.InterstisClient") as mock_client,
        patch("core.sources.resana.backend.ResanaLockClient") as mock_lock,
        patch("core.sources.resana.backend.ResanaMembersClient") as mock_members,
    ):
        mock_tm.return_value.get_valid_token.return_value = "tok"
        backend.resume_export(workspace)
        backend.download_file(source_file, "/tmp/doc.pdf")

    mock_tm.assert_called_with(workspace.migration_user)
    mock_client.return_value.download_file.assert_called_once_with(
        "file-uuid", "/tmp/doc.pdf"
    )
    # Locking is begin_export()'s job, done once for the whole run.
    mock_lock.assert_not_called()
    mock_members.assert_not_called()
