"""Outgoing email (SMTP) settings."""

from configurations import values


class EmailMixin:
    """SMTP backend credentials and the default ``from`` address."""

    EMAIL_BACKEND = values.Value("django.core.mail.backends.smtp.EmailBackend")
    EMAIL_HOST = values.Value(None)
    EMAIL_HOST_USER = values.Value(None)
    EMAIL_HOST_PASSWORD = values.Value(None)
    EMAIL_PORT = values.PositiveIntegerValue(None)
    EMAIL_USE_TLS = values.BooleanValue(False)
    EMAIL_USE_SSL = values.BooleanValue(False)
    EMAIL_FROM = values.Value("from@example.com")
