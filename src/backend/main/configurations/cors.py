"""Cross-Origin Resource Sharing (CORS) settings."""

from configurations import values


class CorsMixin:
    """django-cors-headers configuration for the frontend origins."""

    CORS_ALLOW_CREDENTIALS = True
    CORS_ALLOW_ALL_ORIGINS = values.BooleanValue(True)
    CORS_ALLOWED_ORIGINS = values.ListValue([])
    CORS_ALLOWED_ORIGIN_REGEXES = values.ListValue([])
