from django.conf import settings

import boto3

from core.models import Workspace


class S3ResanaManager:
    def get_bucket(self, workspace):  # pylint: disable=unused-argument
        # TODO: Use mapping # pylint: disable=fixme
        return settings.RESANA_S3_BUCKET

    def get_destination_path(self, workspace: Workspace):
        return f"workspace_{workspace.id}"

    def get_s3_bucket(self, workspace=None):
        s3 = boto3.resource(
            "s3",
            endpoint_url=settings.RESANA_S3_ENDPOINT_URL,
            aws_access_key_id=settings.RESANA_S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.RESANA_S3_SECRET_ACCESS_KEY,
            region_name=settings.RESANA_S3_REGION,
        )
        return s3.Bucket(self.get_bucket(workspace))

    def upload_file(self, path: str, s3_key: str) -> None:
        self.get_s3_bucket().upload_file(path, s3_key)

    def put_object(self, s3_key: str, body: bytes) -> None:
        self.get_s3_bucket().put_object(Key=s3_key, Body=body)

    def delete_prefix(self, prefix: str) -> None:
        """Delete every object under the prefix, taken as a folder."""
        bucket = self.get_s3_bucket()
        bucket.objects.filter(Prefix=prefix.rstrip("/") + "/").delete()
