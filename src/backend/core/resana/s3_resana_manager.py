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
        return settings.RESANA_S3_BUCKET

    def get_destination_path(self, workspace: Workspace):
        return f"workspace_{workspace.id}"

    def list_bucket(self, bucket):
        logger.info(f"Listing bucket {bucket.name} ...")
        for obj in bucket.objects.all():
            logger.info(obj.key)

    def upload_folder(self, workspace: Workspace):
        s3 = boto3.resource(
            "s3",
            endpoint_url=settings.RESANA_S3_ENDPOINT_URL,
            aws_access_key_id=settings.RESANA_S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.RESANA_S3_SECRET_ACCESS_KEY,
            region_name=settings.RESANA_S3_REGION,
        )

        bucket_name = self.get_bucket(workspace)
        bucket = s3.Bucket(bucket_name)
        if bucket.creation_date is None:
            raise Exception(f"Bucket {bucket_name} does not exist")  # pylint: disable=broad-exception-raised

        # self.list_bucket(bucket)

        folder_creator = FolderCreator()
        path = folder_creator.get_workspace_path(workspace)
        destination_path = self.get_destination_path(workspace)
        for root, _dir, files in os.walk(path):
            for name in files:
                relative_path = root.replace(path, "")
                # lstrip is needed because os.path.join will ignore the first argument if it starts with os.sep
                relative_file_path = os.path.join(
                    destination_path, relative_path.lstrip(os.sep), name
                )
                absolute_file_path = os.path.join(root, name)
                logger.info(
                    f"Uploading {absolute_file_path} to {relative_file_path} ...."
                )
                bucket.upload_file(absolute_file_path, relative_file_path)

        # self.list_bucket(bucket)

        return destination_path
