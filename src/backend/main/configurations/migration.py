"""Migration pipeline settings: source/destination backends and tuning."""

from configurations import values


class MigrationBackendsMixin:
    """Which source/destination backends to use and cross-cutting migration knobs."""

    S3_VERSIONS_PAGE_SIZE = 50

    # Source and destination backends
    SOURCE_BACKEND = values.Value(
        "core.sources.osmose.backend.OsmoseSourceBackend",
        environ_name="SOURCE_BACKEND",
        environ_prefix=None,
    )
    DESTINATION_BACKENDS = values.ListValue(
        [
            "core.destinations.archive.backend.ArchiveDestinationBackend",
            "core.destinations.resana.backend.ResanaDestinationBackend",
            "core.destinations.drive.backend.DriveDestinationBackend",
        ],
        environ_name="DESTINATION_BACKENDS",
        environ_prefix=None,
    )
    EXTRA_API_URL_MODULES = values.ListValue(
        [],
        environ_name="EXTRA_API_URL_MODULES",
        environ_prefix=None,
    )
    # 0 = unlimited. When > 0, migrations only download the first N files per
    # workspace (source structure fetch is unaffected, only file downloads are capped).
    MIGRATION_FILE_LIMIT_PER_WORKSPACE = values.IntegerValue(
        0,
        environ_name="MIGRATION_FILE_LIMIT_PER_WORKSPACE",
        environ_prefix=None,
    )
    # Files failing in a row (rejections aside) after which a file-by-file
    # destination is deemed unusable and gets no more files of the run.
    MIGRATION_DESTINATION_MAX_CONSECUTIVE_FAILURES = values.PositiveIntegerValue(
        10,
        environ_name="MIGRATION_DESTINATION_MAX_CONSECUTIVE_FAILURES",
        environ_prefix=None,
    )
    # The file tasks of a run are queued one batch at a time, the next batch once
    # the previous one is done, so other migrations get workers in between.
    # Bytes are the sizes the source announces. 0 = no limit; both 0 queue the
    # whole run at once.
    MIGRATION_BATCH_MAX_FILES = values.PositiveIntegerValue(
        100,
        environ_name="MIGRATION_BATCH_MAX_FILES",
        environ_prefix=None,
    )
    MIGRATION_BATCH_MAX_BYTES = values.PositiveIntegerValue(
        1024**3,
        environ_name="MIGRATION_BATCH_MAX_BYTES",
        environ_prefix=None,
    )
    # A run without news for this long is stalled, see core.processing.watchdog:
    # its unfinished files are queued again, up to MIGRATION_RUN_MAX_REDISPATCHES
    # times, then it fails. Keep it above the transfer of the biggest file.
    MIGRATION_RUN_STALL_SECONDS = values.PositiveIntegerValue(
        3 * 3600,
        environ_name="MIGRATION_RUN_STALL_SECONDS",
        environ_prefix=None,
    )
    MIGRATION_RUN_MAX_REDISPATCHES = values.PositiveIntegerValue(
        2,
        environ_name="MIGRATION_RUN_MAX_REDISPATCHES",
        environ_prefix=None,
    )

    # Archive downloads streamed at once, each holding a gunicorn thread for
    # its whole length: beyond, a download is refused, so the API keeps threads.
    ARCHIVE_MAX_CONCURRENT_DOWNLOADS = values.PositiveIntegerValue(
        12,
        environ_name="ARCHIVE_MAX_CONCURRENT_DOWNLOADS",
        environ_prefix=None,
    )

    # Filesystem source backend
    FILESYSTEM_SOURCE_ROOT = values.Value(
        environ_name="FILESYSTEM_SOURCE_ROOT", environ_prefix=None, default="/demo-data"
    )

    # Fernet key (URL-safe base64, 32 bytes) used to encrypt OIDC tokens at rest.
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    OIDC_TOKENS_ENCRYPTION_KEY = values.Value(
        environ_name="OIDC_TOKENS_ENCRYPTION_KEY", environ_prefix=None
    )
