"""Tests for ArchiveManager and ProgressPercentage."""

# pylint: disable=protected-access
# Reaching into ProgressPercentage's internal counters is the only way to assert
# its progress-tracking state from the outside.

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


def test_upload_file_sends_the_file_under_the_given_key(tmp_path, settings):
    """upload_file() uploads a local file to the bucket under the given key."""
    settings.AWS_STORAGE_BUCKET_NAME = "my-bucket"
    path = tmp_path / "rejected.zip"
    path.write_bytes(b"zip")

    with patch("core.processing.folder_helper.boto3") as mock_boto3:
        ArchiveManager().upload_file(str(path), "some-key.zip")

    mock_s3 = mock_boto3.resource.return_value
    mock_s3.Bucket.assert_called_once_with("my-bucket")
    args, _ = mock_s3.Bucket.return_value.upload_file.call_args
    assert args == (str(path), "some-key.zip")


def test_get_presigned_url_signs_the_given_key(settings):
    """get_presigned_url() signs any key with the short expiry."""
    settings.AWS_S3_DOWNLOAD_URL = ""
    settings.AWS_STORAGE_BUCKET_NAME = "my-bucket"

    with patch("core.processing.folder_helper.boto3") as mock_boto3:
        mock_client = mock_boto3.resource.return_value.meta.client
        mock_client.generate_presigned_url.return_value = "http://minio/k?sig=1"
        url = ArchiveManager().get_presigned_url("other-key.zip")

    assert url == "http://minio/k?sig=1"
    mock_client.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "my-bucket", "Key": "other-key.zip"},
        ExpiresIn=ArchiveManager.download_url_expires_in,
    )


def test_delete_file_removes_the_given_key(settings):
    """delete_file() deletes the object stored under the given key."""
    settings.AWS_STORAGE_BUCKET_NAME = "my-bucket"

    with patch("core.processing.folder_helper.boto3") as mock_boto3:
        ArchiveManager().delete_file("some-key.zip")

    mock_boto3.resource.return_value.meta.client.delete_object.assert_called_once_with(
        Bucket="my-bucket", Key="some-key.zip"
    )


def test_put_object_stores_the_bytes_under_the_given_key(settings):
    """put_object() stores in-memory content under the given key."""
    settings.AWS_STORAGE_BUCKET_NAME = "my-bucket"

    with patch("core.processing.folder_helper.boto3") as mock_boto3:
        ArchiveManager().put_object("archives/a/manifest.json", b"{}")

    mock_s3 = mock_boto3.resource.return_value
    mock_s3.Bucket.assert_called_once_with("my-bucket")
    mock_s3.Bucket.return_value.put_object.assert_called_once_with(
        Key="archives/a/manifest.json", Body=b"{}"
    )


def test_delete_prefix_removes_every_object_under_it(settings):
    """delete_prefix() deletes the objects under the prefix, and only them:
    the prefix is a folder, so "archives/a" does not match "archives/ab"."""
    settings.AWS_STORAGE_BUCKET_NAME = "my-bucket"

    with patch("core.processing.folder_helper.boto3") as mock_boto3:
        ArchiveManager().delete_prefix("archives/a")

    objects = mock_boto3.resource.return_value.Bucket.return_value.objects
    objects.filter.assert_called_once_with(Prefix="archives/a/")
    objects.filter.return_value.delete.assert_called_once_with()


def test_read_object_returns_the_content_of_the_key(settings):
    """read_object() returns the whole content stored under the key."""
    settings.AWS_STORAGE_BUCKET_NAME = "my-bucket"

    with patch("core.processing.folder_helper.boto3") as mock_boto3:
        client = mock_boto3.resource.return_value.meta.client
        client.get_object.return_value["Body"].read.return_value = b"{}"
        content = ArchiveManager().read_object("archives/a/manifest.json")

    assert content == b"{}"
    client.get_object.assert_called_once_with(
        Bucket="my-bucket", Key="archives/a/manifest.json"
    )


def test_iter_object_streams_the_key_only_once_iterated(settings):
    """iter_object() reads the object chunk by chunk, and does not reach S3
    before the first chunk is wanted."""
    settings.AWS_STORAGE_BUCKET_NAME = "my-bucket"

    with patch("core.processing.folder_helper.boto3") as mock_boto3:
        client = mock_boto3.resource.return_value.meta.client
        client.get_object.return_value["Body"].iter_chunks.return_value = iter(
            [b"ab", b"c"]
        )
        chunks = ArchiveManager().iter_object("archives/a/files/1")
        client.get_object.assert_not_called()

        assert list(chunks) == [b"ab", b"c"]

    client.get_object.assert_called_once_with(
        Bucket="my-bucket", Key="archives/a/files/1"
    )
