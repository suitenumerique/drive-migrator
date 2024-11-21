import mimetypes
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

    def list_bucket_prefix(self, bucket, prefix):
        logger.info(f"Listing bucket {bucket.name} with prefix {prefix} ...")
        for obj in bucket.objects.filter(Prefix=prefix):
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

        folder_creator = FolderCreator()
        path = folder_creator.get_workspace_path(workspace)
        destination_path = self.get_destination_path(workspace)

        logger.info(f"Deleting objects with prefix {destination_path}")
        bucket.objects.filter(Prefix=destination_path).delete()
        logger.info(f"Listing {destination_path} after deletion ...")
        self.list_bucket_prefix(bucket, destination_path)

        count = 0
        excluded_mime_types = ["text/html"]

        for root, _dir, files in os.walk(path):
            for name in files:
                count += 1
                relative_path = root.replace(path, "")
                # lstrip is needed because os.path.join will ignore the first argument if it starts with os.sep
                relative_file_path = os.path.join(
                    destination_path, relative_path.lstrip(os.sep), name
                )
                absolute_file_path = os.path.join(root, name)

                mime_type, _ = mimetypes.guess_type(name)
                if mime_type in excluded_mime_types:
                    logger.info(
                        f"({count}) Skipping {absolute_file_path} due to mime type {mime_type}"
                    )
                    continue  # Skip files with excluded mime types

                logger.info(
                    f"({count}) Uploading {absolute_file_path} to {relative_file_path} ...."
                )

                bucket.upload_file(absolute_file_path, relative_file_path)

        # self.list_bucket(bucket)

        return destination_path
