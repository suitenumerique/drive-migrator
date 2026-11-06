"""Static/media files and S3 object storage settings."""

import os

from configurations import values

from .paths import DATA_DIR


class StorageMixin:
    """Static and media file locations plus the default S3 storage backend."""

    # Static files (CSS, JavaScript, Images)
    STATIC_URL = "/static/"
    STATIC_ROOT = os.path.join(DATA_DIR, "static")
    MEDIA_URL = "/media/"
    MEDIA_ROOT = os.path.join(DATA_DIR, "media")

    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
        },
        "staticfiles": {
            "BACKEND": values.Value(
                "whitenoise.storage.CompressedManifestStaticFilesStorage",
                environ_name="STORAGES_STATICFILES_BACKEND",
                environ_prefix=None,
            ),
        },
    }

    # Media
    AWS_S3_ENDPOINT_URL = values.Value(
        environ_name="AWS_S3_ENDPOINT_URL", environ_prefix=None
    )
    AWS_S3_DOWNLOAD_URL = values.Value(
        environ_name="AWS_S3_DOWNLOAD_URL", environ_prefix=None
    )
    AWS_S3_ACCESS_KEY_ID = values.Value(
        environ_name="AWS_S3_ACCESS_KEY_ID", environ_prefix=None
    )
    AWS_S3_SECRET_ACCESS_KEY = values.Value(
        environ_name="AWS_S3_SECRET_ACCESS_KEY", environ_prefix=None
    )
    AWS_S3_REGION_NAME = values.Value(
        environ_name="AWS_S3_REGION_NAME", environ_prefix=None
    )
    AWS_STORAGE_BUCKET_NAME = values.Value(
        "main-media-storage",
        environ_name="AWS_STORAGE_BUCKET_NAME",
        environ_prefix=None,
    )
