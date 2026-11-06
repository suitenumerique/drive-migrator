"""Resana / Interstis source backend settings."""

from configurations import values


class ResanaMixin:
    """Resana S3, authentication, API endpoints and the resana-auth bridge."""

    RESANA_S3_ENDPOINT_URL = values.Value(
        environ_name="RESANA_S3_ENDPOINT_URL", environ_prefix=None
    )
    RESANA_S3_ACCESS_KEY_ID = values.Value(
        environ_name="RESANA_S3_ACCESS_KEY_ID", environ_prefix=None
    )
    RESANA_S3_SECRET_ACCESS_KEY = values.Value(
        environ_name="RESANA_S3_SECRET_ACCESS_KEY", environ_prefix=None
    )
    RESANA_AUTH_ENDPOINT = values.Value(
        environ_name="RESANA_AUTH_ENDPOINT", environ_prefix=None
    )
    RESANA_AUTH_USER = values.Value(
        environ_name="RESANA_AUTH_USER", environ_prefix=None
    )
    RESANA_AUTH_PASSWORD = values.Value(
        environ_name="RESANA_AUTH_PASSWORD", environ_prefix=None
    )
    RESANA_API_ENDPOINT = values.Value(
        environ_name="RESANA_API_ENDPOINT", environ_prefix=None
    )
    RESANA_WEB_ENDPOINT = values.Value(
        environ_name="RESANA_WEB_ENDPOINT", environ_prefix=None
    )
    RESANA_ALT_API_ENDPOINT = values.Value(
        environ_name="RESANA_ALT_API_ENDPOINT", environ_prefix=None
    )
    RESANA_KEYCLOAK_ENDPOINT = values.Value(
        environ_name="RESANA_KEYCLOAK_ENDPOINT", environ_prefix=None
    )
    RESANA_MIGRATOR_CLIENT_ID = values.Value(
        "resana-migrator", environ_name="RESANA_MIGRATOR_CLIENT_ID", environ_prefix=None
    )
    RESANA_MIGRATOR_CLIENT_SECRET = values.Value(
        environ_name="RESANA_MIGRATOR_CLIENT_SECRET", environ_prefix=None
    )
    RESANA_MIGRATOR_REDIRECT_URI = values.Value(
        environ_name="RESANA_MIGRATOR_REDIRECT_URI", environ_prefix=None
    )
    RESANA_MIGRATOR_STATE_TTL = values.IntegerValue(
        600, environ_name="RESANA_MIGRATOR_STATE_TTL", environ_prefix=None
    )
    # Frontend pages the resana-auth callback redirects to once the bridge
    # session has been created (or failed to be).
    RESANA_MIGRATOR_REDIRECT_URL_SUCCESS = values.Value(
        None, environ_name="RESANA_MIGRATOR_REDIRECT_URL_SUCCESS", environ_prefix=None
    )
    RESANA_MIGRATOR_REDIRECT_URL_FAILURE = values.Value(
        None, environ_name="RESANA_MIGRATOR_REDIRECT_URL_FAILURE", environ_prefix=None
    )
    RESANA_S3_BUCKET = values.Value(
        environ_name="RESANA_S3_BUCKET", environ_prefix=None
    )
    RESANA_S3_REGION = values.Value(
        environ_name="RESANA_S3_REGION", environ_prefix=None
    )
    RESANA_DEFAULT_ORGANIZATION = values.Value(
        environ_name="RESANA_DEFAULT_ORGANIZATION", environ_prefix=None
    )
    # Personal workspaces are excluded from migration by default (issue #163).
    # Set to True to offer them for migration again.
    RESANA_MIGRATE_PERSONAL_WORKSPACES = values.BooleanValue(
        False, environ_name="RESANA_MIGRATE_PERSONAL_WORKSPACES", environ_prefix=None
    )
    # Retry tuning for transient network errors on Resana/Interstis API calls.
    RESANA_RETRY_MAX_ATTEMPTS = values.IntegerValue(
        3, environ_name="RESANA_RETRY_MAX_ATTEMPTS", environ_prefix=None
    )
    RESANA_RETRY_WAIT_MULTIPLIER = values.IntegerValue(
        2, environ_name="RESANA_RETRY_WAIT_MULTIPLIER", environ_prefix=None
    )
    RESANA_RETRY_WAIT_MIN = values.IntegerValue(
        2, environ_name="RESANA_RETRY_WAIT_MIN", environ_prefix=None
    )
