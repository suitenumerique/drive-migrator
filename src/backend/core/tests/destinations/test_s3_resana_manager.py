"""Tests for S3ResanaManager, the client of the bucket Resana imports from."""

from unittest.mock import patch

import pytest

from core.destinations.resana.s3_resana_manager import S3ResanaManager


@pytest.fixture(name="boto3")
def fixture_boto3(settings):
    """boto3 behind S3ResanaManager, with the Resana bucket settings."""
    settings.RESANA_S3_ENDPOINT_URL = "https://s3.resana.example"
    settings.RESANA_S3_ACCESS_KEY_ID = "key-id"
    settings.RESANA_S3_SECRET_ACCESS_KEY = "secret"
    settings.RESANA_S3_REGION = "eu-west-2"
    settings.RESANA_S3_BUCKET = "resana-bucket"
    with patch("core.destinations.resana.s3_resana_manager.boto3") as mock_boto3:
        yield mock_boto3


def test_operations_reach_the_resana_bucket(boto3):
    """Files go to Resana's own S3, not to the migrator's bucket."""
    S3ResanaManager().put_object("k", b"x")

    boto3.resource.assert_called_once_with(
        "s3",
        endpoint_url="https://s3.resana.example",
        aws_access_key_id="key-id",
        aws_secret_access_key="secret",
        region_name="eu-west-2",
    )
    boto3.resource.return_value.Bucket.assert_called_once_with("resana-bucket")


def test_upload_file_sends_the_file_under_the_given_key(boto3, tmp_path):
    """upload_file() uploads a local file under the given key."""
    path = tmp_path / "report.pdf"
    path.write_bytes(b"pdf")

    S3ResanaManager().upload_file(str(path), "workspace_1/docs/report.pdf")

    bucket = boto3.resource.return_value.Bucket.return_value
    bucket.upload_file.assert_called_once_with(str(path), "workspace_1/docs/report.pdf")


def test_put_object_stores_the_bytes_under_the_given_key(boto3):
    """put_object() stores in-memory content under the given key."""
    S3ResanaManager().put_object("workspace_1/osmose_users.csv", b"a,b,c\r\n")

    bucket = boto3.resource.return_value.Bucket.return_value
    bucket.put_object.assert_called_once_with(
        Key="workspace_1/osmose_users.csv", Body=b"a,b,c\r\n"
    )


def test_delete_prefix_removes_every_object_under_it(boto3):
    """delete_prefix() takes the prefix as a folder: workspace_1 never matches
    workspace_10."""
    S3ResanaManager().delete_prefix("workspace_1")

    bucket = boto3.resource.return_value.Bucket.return_value
    bucket.objects.filter.assert_called_once_with(Prefix="workspace_1/")
    bucket.objects.filter.return_value.delete.assert_called_once_with()
