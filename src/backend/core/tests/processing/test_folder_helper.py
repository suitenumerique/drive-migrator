"""Tests for ArchiveManager and ProgressPercentage."""

# pylint: disable=protected-access
# Reaching into ProgressPercentage's internal counters is the only way to assert
# its progress-tracking state from the outside.

import os
from unittest.mock import MagicMock, patch

import pytest

from core.models import Workspace
from core.processing.folder_helper import ArchiveManager, ProgressPercentage

# ---------------------------------------------------------------------------
# ProgressPercentage
# ---------------------------------------------------------------------------


def test_progress_percentage_init(tmp_path):
    """ProgressPercentage reads the file size on initialisation."""
    f = tmp_path / "archive.zip"
    f.write_bytes(b"x" * 1024)

    pp = ProgressPercentage(str(f))

    assert pp._size == 1024
    assert pp._seen_so_far == 0
    assert pp._last_update_time == 0


def test_progress_percentage_call_accumulates_bytes(tmp_path):
    """__call__() accumulates the transferred byte count."""
    f = tmp_path / "archive.zip"
    f.write_bytes(b"x" * 2048)

    pp = ProgressPercentage(str(f))
    pp(512)
    pp(512)

    assert pp._seen_so_far == 1024


def test_progress_percentage_logs_when_throttle_elapsed(tmp_path):
    """__call__() logs progress when more than 30 s have passed since last log."""
    f = tmp_path / "archive.zip"
    f.write_bytes(b"x" * 1000)

    pp = ProgressPercentage(str(f))
    # Simulate that last update was 31 seconds ago
    pp._last_update_time = 0

    with patch("core.processing.folder_helper.time") as mock_time:
        mock_time.time.return_value = 31.0

        with patch.object(pp, "_lock"):
            pp(500)

    assert pp._seen_so_far == 500


def test_progress_percentage_skips_log_within_throttle(tmp_path):
    """__call__() does not log when called again within the 30 s throttle window."""
    f = tmp_path / "archive.zip"
    f.write_bytes(b"x" * 1000)

    pp = ProgressPercentage(str(f))
    pp._last_update_time = 100.0

    with (
        patch("core.processing.folder_helper.time") as mock_time,
        patch("core.processing.folder_helper.logger") as mock_logger,
    ):
        mock_time.time.return_value = 110.0  # only 10 s elapsed → no log
        pp(200)

    mock_logger.info.assert_not_called()


# ---------------------------------------------------------------------------
# ArchiveManager — local filesystem methods
# ---------------------------------------------------------------------------


def test_get_archive_path(tmp_path, settings):
    """get_archive_path() returns the workspace folder path with a .zip suffix."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = MagicMock(spec=Workspace)
    workspace.id = "ws-arch"

    manager = ArchiveManager()
    path = manager.get_archive_path(workspace)

    assert path == str(tmp_path / "workspace_ws-arch.zip")


def test_zip_workspace_folder(tmp_path, settings):
    """zip_workspace_folder() calls shutil.make_archive on the workspace path."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = MagicMock(spec=Workspace)
    workspace.id = "ws-zip"
    workspace_dir = tmp_path / "workspace_ws-zip"
    workspace_dir.mkdir()
    (workspace_dir / "file.txt").write_text("content")

    manager = ArchiveManager()
    manager.zip_workspace_folder(workspace)

    assert os.path.exists(str(tmp_path / "workspace_ws-zip.zip"))


def test_delete_archive_removes_existing_file(tmp_path, settings):
    """delete_archive() removes the zip file if it exists."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = MagicMock(spec=Workspace)
    workspace.id = "ws-del"
    zip_path = tmp_path / "workspace_ws-del.zip"
    zip_path.write_bytes(b"data")

    manager = ArchiveManager()
    manager.delete_archive(workspace)

    assert not zip_path.exists()


def test_delete_archive_is_safe_when_missing(tmp_path, settings):
    """delete_archive() does nothing if the zip file does not exist."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = MagicMock(spec=Workspace)
    workspace.id = "ws-del-missing"

    manager = ArchiveManager()
    manager.delete_archive(workspace)  # must not raise


# ---------------------------------------------------------------------------
# ArchiveManager — S3 methods
# ---------------------------------------------------------------------------


def test_get_s3_resource_creates_boto3_resource(settings):
    """get_s3_resource() creates a boto3 S3 resource with the configured credentials."""
    settings.AWS_S3_ENDPOINT_URL = "http://minio:9000"
    settings.AWS_S3_ACCESS_KEY_ID = "key"
    settings.AWS_S3_SECRET_ACCESS_KEY = "secret"

    with patch("core.processing.folder_helper.boto3") as mock_boto3:
        manager = ArchiveManager()
        manager.get_s3_resource()

    mock_boto3.resource.assert_called_once_with(
        "s3",
        endpoint_url="http://minio:9000",
        aws_access_key_id="key",
        aws_secret_access_key="secret",
    )


def test_get_download_url_raises_when_no_s3_key():
    """get_download_url() raises ValueError when the workspace has no archive s3_key."""
    workspace = MagicMock(spec=Workspace)
    workspace.get_destination_metadata.return_value = {}

    manager = ArchiveManager()
    with pytest.raises(ValueError, match="does not have an archive path"):
        manager.get_download_url(workspace)


def test_get_download_url_uses_short_expiry(settings):
    """get_download_url() requests a short-lived presigned URL (not 7 days) so a leaked
    email link/log entry can't be used to download the archive long after the fact."""
    settings.AWS_S3_DOWNLOAD_URL = ""
    workspace = MagicMock(spec=Workspace)
    workspace.get_destination_metadata.return_value = {"s3_key": "ws.zip"}

    with patch("core.processing.folder_helper.boto3") as mock_boto3:
        mock_client = mock_boto3.resource.return_value.meta.client
        mock_client.generate_presigned_url.return_value = (
            "http://minio:9000/bucket/ws.zip?sig=abc"
        )
        manager = ArchiveManager()
        manager.get_download_url(workspace)

    _, kwargs = mock_client.generate_presigned_url.call_args
    assert kwargs["ExpiresIn"] == ArchiveManager.download_url_expires_in
    assert kwargs["ExpiresIn"] <= 300


def test_get_download_url_returns_raw_url_without_download_url_setting(settings):
    """get_download_url() returns the presigned URL as-is when AWS_S3_DOWNLOAD_URL is unset."""
    settings.AWS_S3_DOWNLOAD_URL = ""
    workspace = MagicMock(spec=Workspace)
    workspace.get_destination_metadata.return_value = {"s3_key": "ws.zip"}

    presigned_url = "http://minio:9000/bucket/ws.zip?sig=abc"

    with patch("core.processing.folder_helper.boto3") as mock_boto3:
        mock_boto3.resource.return_value.meta.client.generate_presigned_url.return_value = presigned_url
        manager = ArchiveManager()
        result = manager.get_download_url(workspace)

    assert result == presigned_url


def test_get_download_url_replaces_netloc_when_download_url_setting_set(settings):
    """get_download_url() replaces the URL netloc with the one from AWS_S3_DOWNLOAD_URL."""
    settings.AWS_S3_DOWNLOAD_URL = "http://localhost:9001"
    workspace = MagicMock(spec=Workspace)
    workspace.get_destination_metadata.return_value = {"s3_key": "ws.zip"}

    presigned_url = "http://minio:9000/bucket/ws.zip?sig=abc"

    with patch("core.processing.folder_helper.boto3") as mock_boto3:
        mock_boto3.resource.return_value.meta.client.generate_presigned_url.return_value = presigned_url
        manager = ArchiveManager()
        result = manager.get_download_url(workspace)

    assert result.startswith("http://localhost:9001")
    assert "sig=abc" in result


def test_upload_archive_uploads_file_and_returns_url(tmp_path, settings):
    """upload_archive() uploads the zip to S3, stores s3_key in metadata, and returns URL."""
    settings.APP_WORK_DIR = str(tmp_path)
    settings.AWS_STORAGE_BUCKET_NAME = "my-bucket"
    workspace = MagicMock(spec=Workspace)
    workspace.id = "ws-up"
    workspace.get_destination_metadata.return_value = {"s3_key": "workspace_ws-up.zip"}

    # Create a real zip file for ProgressPercentage to stat
    zip_path = tmp_path / "workspace_ws-up.zip"
    zip_path.write_bytes(b"fake-zip-data")

    expected_url = "http://localhost:9001/bucket/ws.zip?sig=xyz"

    with patch("core.processing.folder_helper.boto3") as mock_boto3:
        mock_s3 = mock_boto3.resource.return_value
        mock_s3.meta.client.generate_presigned_url.return_value = expected_url

        with patch.object(
            ArchiveManager, "get_download_url", return_value=expected_url
        ):
            manager = ArchiveManager()
            url = manager.upload_archive(workspace)

    mock_s3.Bucket.return_value.upload_file.assert_called_once()
    workspace.set_destination_metadata.assert_called_once_with(
        "archive", {"s3_key": "workspace_ws-up.zip"}
    )
    workspace.save.assert_called_once()
    assert url == expected_url
