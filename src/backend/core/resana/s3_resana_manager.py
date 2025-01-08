import os

from django.conf import settings

import boto3
import magic
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

    def upload_validation(self, count, absolute_file_path):
        allowed = [
            "application/x-7z-compressed",
            "application/x-freearc",
            "application/x-bzip",
            "application/x-bzip2",
            "application/vnd.rar",
            "application/zip",
            "application/x-xz-compressed-tar",
            "application/x-compressed-tar",
            "application/octet-stream",
            "application/x-tar",
            "application/x-zip-compressed",
            "application/x-compressed",
            "application/x-gzip",
            "application/x-rar",
            "application/x-rar-compressed",
            "application/gzip",
            "text/csv",
            "application/csv",
            "text/x-csv",
            "application/x-csv",
            "text/x-comma-separated-values",
            "text/comma-separated-values",
            "text/x-c",
            "text/x-Algol68",
            "image/vnd.djvu",
            "application/x-dbf",
            "application/encrypted",
            "application/msaccess",
            "application/x-msaccess",
            "application/x-msmetafile",
            "application/msword",
            "application/vnd.ms-word",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
            "application/vnd.ms-word.document.macroEnabled.12",
            "application/vnd.ms-word.template.macroEnabled.12",
            "application/vnd.ms-powerpoint.template.macroenabled.12",
            "application/vnd.openxmlformats-officedocument.presentationml.template",
            "application/vnd.ms-powerpoint.slideshow.macroEnabled.12",
            "application/vnd.openxmlformats-officedocument.presentationml.slideshow",
            "application/vnd.ms-powerpoint",
            "application/vnd.ms-powerpoint.presentation.macroEnabled.12",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/x-mspublisher",
            "application/vnd.ms-excel",
            "application/vnd.ms-excel.sheet.macroEnabled.12",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel.template.macroEnabled.12",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.template",
            "application/vnd.ms-xpsdocument",
            "application/vnd.ms-excel.sheet.binary.macroEnabled.12",
            "application/vnd.ms-officetheme",
            "application/vnd.ms-office",
            "application/vnd.ms-pki.stl",
            "application/vnd.ms-project",
            "application/vnd.ms-wpl",
            "application/vnd.visio",
            "image/x-dwg",
            "image/vnd.dwg",
            "drawing/dwg",
            "application/acad",
            "application/x-acad",
            "application/autocad_dwg",
            "application/dwg",
            "application/x-dwg",
            "application/x-autocad",
            "message/rfc822",
            "application/epub+zip",
            "application/vnd.oasis.opendocument.tex",
            "application/vnd.oasis.opendocument.text-template",
            "application/vnd.oasis.opendocument.text-master",
            "application/vnd.oasis.opendocument.text-web",
            "application/vnd.oasis.opendocument.spreadsheet",
            "application/vnd.oasis.opendocument.spreadsheet-template",
            "application/vnd.oasis.opendocument.graphics",
            "application/vnd.oasis.opendocument.graphics-template",
            "application/vnd.oasis.opendocument.presentation",
            "application/vnd.oasis.opendocument.presentation-template",
            "application/vnd.oasis.opendocument.chart",
            "application/vnd.oasis.opendocument.chart-template",
            "application/vnd.oasis.opendocument.image",
            "application/vnd.oasis.opendocument.image-template",
            "application/vnd.oasis.opendocument.formula",
            "application/vnd.oasis.opendocument.formula-template",
            "application/vnd.oasis.opendocument.base",
            "application/vnd.oasis.opendocument.database",
            "application/vnd.openofficeorg.extension",
            "application/x-freeplane",
            "text/html",
            "application/vnd.ms-outlook",
            "application/pdf",
            "application/marc",
            "image/x-xcf",
            "image/x-eps",
            "image/jp2",
            "application/vnd.adobe.pdfxml",
            "audio/webm",
            "video/webm",
            "image/webp",
            "video/3gpp",
            "text/x-tex",
            "application/vnd.api+json",
            "application/json",
            "audio/basic",
            "audio/aac",
            "audio/aiff",
            "audio/x-aiff",
            "audio/m4a",
            "audio/x-m4a",
            "audio/mid",
            "audio/mpeg",
            "audio/wav",
            "audio/vnd.wav",
            "audio/x-wav",
            "audio/x-mpegurl",
            "audio/x-ms-wma",
            "application/vnd.ms-fontobject",
            "font/otf",
            "font/ttf",
            "font/woff",
            "font/woff2",
            "image/vnd.microsoft.icon",
            "image/x-icon",
            "text/x-tex",
            "image/gif",
            "image/jpeg",
            "image/png",
            "image/bmp",
            "image/tiff",
            "image/svg+xml",
            "image/svg",
            "image/x-ms-bmp",
            "text/rtf",
            "text/plain",
            "text/x-ascii-art",
            "inode/x-empty",
            "application/rtf",
            "text/vbscript",
            "text/x-component",
            "text/xml",
            "text/x-vcard",
            "text/x-python",
            "video/mpeg",
            "video/mp4",
            "audio/mp4",
            "application/mp4",
            "video/mpeg",
            "video/vnd.dlna.mpeg-tts",
            "video/x-flv",
            "video/x-m4v",
            "video/x-msvideo",
            "video/x-ms-wmv",
            "video/quicktime",
            "video/x-matroska",
            "application/oleobject",
            "application/onenote",
            "application/winhlp",
            "application/xaml+xml",
            "application/x-pkcs12",
            "application/x-safari-webarchive",
            "application/CDFV2",
            "application/vnd.android.package-archive",
            "application/x-sqlite3",
            "x-world/x-3dmf",
            "image/vnd.dwg",
            "application/vnd.oasis.opendocument.text",
            "application/x-freeplane",
            "application/postscript",
            "image/vnd.adobe.photoshop",
            "chemical/x-pdb",
            "text/x-shellscript",
            "application/x-freemind",
            "application/x-wine-extension-ini",
            "image/heic",
            "application/x-empty",
            "text/vcard",
            "application/vnd.debian.binary-package",
            "application/vnd.sun.xml.calc",
            "application/vnd.sun.xml.calc.template",
            "application/vnd.sun.xml.draw",
            "application/vnd.sun.xml.draw.template",
            "application/vnd.sun.xml.impress",
            "application/vnd.sun.xml.impress.template",
            "application/vnd.sun.xml.math",
            "application/vnd.sun.xml.writer",
            "application/vnd.sun.xml.writer.global",
            "application/vnd.sun.xml.writer.template",
        ]
        mime_type = magic.from_file(absolute_file_path, mime=True)
        if mime_type not in allowed:
            logger.info(
                f"({count}) Skipping {absolute_file_path} due to mime type {mime_type}"
            )
            return False

        return True

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
        # We can't rely on bucket creation date because it's not available in OutScale;
        # if bucket.creation_date is None:
        #     raise Exception(f"Bucket {bucket_name} does not exist")  # pylint: disable=broad-exception-raised

        folder_creator = FolderCreator()
        path = folder_creator.get_workspace_path(workspace)
        destination_path = self.get_destination_path(workspace)

        logger.info(f"Deleting objects with prefix {destination_path}")
        bucket.objects.filter(Prefix=destination_path).delete()
        logger.info(f"Listing {destination_path} after deletion ...")
        self.list_bucket_prefix(bucket, destination_path)

        count = 0

        for root, _dir, files in os.walk(path):
            for name in files:
                count += 1
                relative_path = root.replace(path, "")
                # lstrip is needed because os.path.join will ignore the first argument if it starts with os.sep
                relative_file_path = os.path.join(
                    destination_path, relative_path.lstrip(os.sep), name
                )
                absolute_file_path = os.path.join(root, name)

                if not self.upload_validation(count, absolute_file_path):
                    continue

                logger.info(
                    f"({count}) Uploading {absolute_file_path} to {relative_file_path} ...."
                )

                bucket.upload_file(absolute_file_path, relative_file_path)

        # self.list_bucket(bucket)

        return destination_path
