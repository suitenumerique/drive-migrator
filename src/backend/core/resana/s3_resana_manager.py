import os

from django.conf import settings

import boto3
from celery.utils.log import get_task_logger

from core.models import Workspace
from core.processing.folder_creator import FolderCreator

logger = get_task_logger(__name__)


class S3ResanaManager:
    def get_bucket(self, workspace):  # pylint: disable=unused-argument
        # TODO: Use mapping # pylint: disable=fixme
        return "test-organization"

    def upload_folder(self, workspace: Workspace):
        s3 = boto3.resource(
            "s3",
            endpoint_url=settings.RESANA_S3_ENDPOINT_URL,
            aws_access_key_id=settings.RESANA_S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.RESANA_S3_SECRET_ACCESS_KEY,
        )

        bucket_name = self.get_bucket(workspace)
        bucket = s3.Bucket(bucket_name)
        if bucket.creation_date is None:
            raise Exception(f"Bucket {bucket_name} does not exist")  # pylint: disable=broad-exception-raised

        bucket.objects.all().delete()

        folder_creator = FolderCreator()
        path = folder_creator.get_workspace_path(workspace)
        for root, _dir, files in os.walk(path):
            for name in files:
                relative_path = root.replace(path, "")
                relative_file_path = os.path.join(relative_path, name)
                absolute_file_path = os.path.join(root, name)
                logger.info(f"Uploading {relative_file_path} ....")
                bucket.upload_file(absolute_file_path, relative_file_path)

        return bucket_name
