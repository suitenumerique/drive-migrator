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

    # Filesystem source backend
    FILESYSTEM_SOURCE_ROOT = values.Value(
        environ_name="FILESYSTEM_SOURCE_ROOT", environ_prefix=None, default="/demo-data"
    )

    # Fernet key (URL-safe base64, 32 bytes) used to encrypt OIDC tokens at rest.
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    OIDC_TOKENS_ENCRYPTION_KEY = values.Value(
        environ_name="OIDC_TOKENS_ENCRYPTION_KEY", environ_prefix=None
    )
