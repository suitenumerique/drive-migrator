"""La Suite Drive destination backend settings."""

from configurations import values


class DriveMixin:
    """Drive URLs, OIDC client, auth/account policy and retry/integrity tuning."""

    # La Suite Drive base URL, used both for backend API calls and as the public
    # URL linked to from the frontend (e.g. end of migration screen).
    DRIVE_BASE_URL = values.Value(
        "", environ_name="DRIVE_BASE_URL", environ_prefix=None
    )
    # Optional overrides when the API and the public frontend are not reachable
    # at the same URL (e.g. an internal network address for backend calls).
    DRIVE_API_BASE_URL = values.Value(
        DRIVE_BASE_URL, environ_name="DRIVE_API_BASE_URL", environ_prefix=None
    )
    DRIVE_FRONTEND_URL = values.Value(
        DRIVE_BASE_URL, environ_name="DRIVE_FRONTEND_URL", environ_prefix=None
    )

    # Help menu documentation link and contact email, displayed by the frontend.
    HELP_DOCUMENTATION_URL = values.Value(
        "https://example.com/help",
        environ_name="HELP_DOCUMENTATION_URL",
        environ_prefix=None,
    )
    HELP_CONTACT_EMAIL = values.Value(
        "help@example.com",
        environ_name="HELP_CONTACT_EMAIL",
        environ_prefix=None,
    )

    DRIVE_OIDC_TOKEN_ENDPOINT = values.Value(
        environ_name="DRIVE_OIDC_TOKEN_ENDPOINT", environ_prefix=None, default=""
    )
    DRIVE_OIDC_CLIENT_ID = values.Value(
        environ_name="DRIVE_OIDC_CLIENT_ID", environ_prefix=None, default=""
    )
    DRIVE_OIDC_CLIENT_SECRET = values.Value(
        environ_name="DRIVE_OIDC_CLIENT_SECRET", environ_prefix=None, default=""
    )
    # "service_account": client_credentials grant via /external_api/v1.0/ (default)
    # "user_token": authenticated user's ProConnect token via /external_api/v1.0/
    DRIVE_AUTH_MODE = values.Value(
        "service_account", environ_name="DRIVE_AUTH_MODE", environ_prefix=None
    )
    # In user_token mode, who creates the user's Drive account:
    # "user_created": the user must log in to Drive once before migrating.
    # "auto_created": Drive creates it on the first migration (needs the
    # resource server to create unknown users, see django-lasuite OIDC_RS_CREATE_USER).
    DRIVE_ACCOUNT_POLICY = values.Value(
        "user_created", environ_name="DRIVE_ACCOUNT_POLICY", environ_prefix=None
    )
    # Set to False to skip sharing migrated files with workspace members.
    # Members will then manage permissions manually in Drive.
    DRIVE_SHARE_MEMBERS = values.BooleanValue(
        True, environ_name="DRIVE_SHARE_MEMBERS", environ_prefix=None
    )
    # Retry tuning for transient network errors on Drive API/S3 calls.
    DRIVE_RETRY_MAX_ATTEMPTS = values.IntegerValue(
        3, environ_name="DRIVE_RETRY_MAX_ATTEMPTS", environ_prefix=None
    )
    DRIVE_RETRY_WAIT_MULTIPLIER = values.IntegerValue(
        2, environ_name="DRIVE_RETRY_WAIT_MULTIPLIER", environ_prefix=None
    )
    DRIVE_RETRY_WAIT_MIN = values.IntegerValue(
        2, environ_name="DRIVE_RETRY_WAIT_MIN", environ_prefix=None
    )
    # Integrity check: how long to wait for Drive's malware analysis of freshly
    # uploaded files to finish, and how often to poll their upload_state.
    DRIVE_INTEGRITY_ANALYSIS_TIMEOUT = values.PositiveIntegerValue(
        300, environ_name="DRIVE_INTEGRITY_ANALYSIS_TIMEOUT", environ_prefix=None
    )
    DRIVE_INTEGRITY_ANALYSIS_POLL_INTERVAL = values.PositiveIntegerValue(
        10, environ_name="DRIVE_INTEGRITY_ANALYSIS_POLL_INTERVAL", environ_prefix=None
    )
