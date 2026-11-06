"""Osmose source backend settings."""

from configurations import values


class OsmoseMixin:
    """Osmose PKI/JWT credentials, API endpoints and download retry tuning."""

    # Osmose
    OSMOSE_PKI_RSA_PRIVATE_KEY = values.Value(
        environ_name="OSMOSE_PKI_RSA_PRIVATE_KEY", environ_prefix=None
    )
    OSMOSE_PKI_RSA_PRIVATE_KEY_PASSPHRASE = values.Value(
        environ_name="OSMOSE_PKI_RSA_PRIVATE_KEY_PASSPHRASE", environ_prefix=None
    )
    OSMOSE_API_ENDPOINT = values.Value(
        environ_name="OSMOSE_API_ENDPOINT", environ_prefix=None
    )
    OSMOSE_BASE_ENDPOINT = values.Value(
        environ_name="OSMOSE_BASE_ENDPOINT", environ_prefix=None
    )
    OSMOSE_JWT_SUB = values.Value(environ_name="OSMOSE_JWT_SUB", environ_prefix=None)
    OSMOSE_JWT_ISS = values.Value(environ_name="OSMOSE_JWT_ISS", environ_prefix=None)
    OSMOSE_JWT_IP_MASK = values.Value(
        environ_name="OSMOSE_JWT_IP_MASK", environ_prefix=None
    )
    OSMOSE_BACKEND = values.Value(environ_name="OSMOSE_BACKEND", environ_prefix=None)
    OSMOSE_BACKEND_DEBUG = values.BooleanValue(
        environ_name="OSMOSE_BACKEND_DEBUG", environ_prefix=None, default=False
    )
    # Prevent from failing the entire task if file downloads gives a 404.
    OSMOSE_BACKEND_ACCEPT_404 = values.BooleanValue(
        environ_name="OSMOSE_BACKEND_ACCEPT_404", environ_prefix=None, default=False
    )
    # Retry tuning for transient network errors on Osmose file downloads.
    OSMOSE_RETRY_MAX_ATTEMPTS = values.IntegerValue(
        5, environ_name="OSMOSE_RETRY_MAX_ATTEMPTS", environ_prefix=None
    )
    OSMOSE_RETRY_WAIT_MULTIPLIER = values.IntegerValue(
        2, environ_name="OSMOSE_RETRY_WAIT_MULTIPLIER", environ_prefix=None
    )
    OSMOSE_RETRY_WAIT_MIN = values.IntegerValue(
        2, environ_name="OSMOSE_RETRY_WAIT_MIN", environ_prefix=None
    )
