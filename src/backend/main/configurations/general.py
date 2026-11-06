"""General Django settings that do not belong to a more specific domain."""

from configurations import values


class GeneralMixin:
    """Core Django plumbing: debug flags, security basics, URLs and the app user."""

    DEBUG = False
    USE_SWAGGER = False

    API_VERSION = "v1.0"

    # Security
    ALLOWED_HOSTS = values.ListValue([])
    SECRET_KEY = values.Value(None)

    # Application definition
    ROOT_URLCONF = "main.urls"
    WSGI_APPLICATION = "main.wsgi.application"

    DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

    SITE_ID = 1

    # App
    APP_EMAIL_FORCE_TO = values.Value(
        environ_name="APP_EMAIL_FORCE_TO", environ_prefix=None
    )
    APP_WORK_DIR = values.Value(environ_name="APP_WORK_DIR", environ_prefix=None)

    AUTH_USER_MODEL = "core.User"
    INVITATION_VALIDITY_DURATION = 604800  # 7 days, in seconds

    INTERNAL_IPS = [
        "127.0.0.1",
    ]

    DEBUG_TOOLBAR_CONFIG = {
        "SHOW_TOOLBAR_CALLBACK": lambda request: True,
    }
