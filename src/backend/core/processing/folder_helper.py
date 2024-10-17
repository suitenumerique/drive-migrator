import os.path
import shutil
import threading
import time
import urllib.parse

from django.conf import settings

import boto3
from celery.utils.log import get_task_logger

from core.models import Workspace
from core.processing.folder_creator import FolderCreator

logger = get_task_logger(__name__)


class ProgressPercentage(object):
    def __init__(self, filename):
        self._filename = filename
        self._size = float(os.path.getsize(filename))
        self._seen_so_far = 0
        self._last_update_time = 0
        self._lock = threading.Lock()

    def __call__(self, bytes_amount):
        # To simplify, assume this is hooked up to a single filename
        with self._lock:
            self._seen_so_far += bytes_amount
            percentage = (self._seen_so_far / self._size) * 100
            current_time = time.time()
            if current_time - self._last_update_time > 30:
                logger.info(
                    "%s  %s / %s  (%.2f%%)"
                    % (self._filename, self._seen_so_far, self._size, percentage)
                )
                self._last_update_time = current_time


class ArchiveManager:
    archive_format = "zip"

    def zip_workspace_folder(self, workspace: Workspace):
        folder_creator = FolderCreator()
        path = folder_creator.get_workspace_path(workspace)
        shutil.make_archive(path, self.archive_format, path)

    def get_archive_path(self, workspace: Workspace):
        folder_creator = FolderCreator()
        return folder_creator.get_workspace_path(workspace) + "." + self.archive_format

    def delete_archive(self, workspace: Workspace):
        path = self.get_archive_path(workspace)
        if os.path.exists(path):
            os.remove(path)

    def upload_archive(self, workspace: Workspace):
        folder_creator = FolderCreator()
        path = folder_creator.get_workspace_path(workspace) + "." + self.archive_format

        s3 = boto3.resource(
            "s3",
            endpoint_url=settings.AWS_S3_ENDPOINT_URL,
            aws_access_key_id=settings.AWS_S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_S3_SECRET_ACCESS_KEY,
        )

        bucket = s3.Bucket(settings.AWS_STORAGE_BUCKET_NAME)
        destination = os.path.basename(path)
        bucket.upload_file(path, destination, Callback=ProgressPercentage(path))

        url = s3.meta.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.AWS_STORAGE_BUCKET_NAME, "Key": destination},
            ExpiresIn=3600 * 24,
        )

        logger.info("ArchiveManager.upload_archive")

        if not settings.AWS_S3_DOWNLOAD_URL:
            logger.info(
                "ArchiveManager.upload_archive returning url without replacing netloc"
            )
            return url

        # This part could look weird but in local when using docker url netloc is "http://minio:9000"
        # which is not reachable outside docker, so we replace it with the "localhost" url in order to be
        # able to download the file locally.
        url_parsed = urllib.parse.urlparse(url)
        download_url_parsed = urllib.parse.urlparse(settings.AWS_S3_DOWNLOAD_URL)
        replaced = url_parsed._replace(netloc=download_url_parsed.netloc)
        logger.info("ArchiveManager.upload_archive returning url with replaced netloc")

        return replaced.geturl()
