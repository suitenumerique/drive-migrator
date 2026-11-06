"""OpenID Connect (Authorization Code Flow) authentication settings."""

from configurations import values


class OIDCMixin:
    """mozilla-django-oidc configuration and the login/logout redirect flow."""

    # OIDC - Authorization Code Flow
    OIDC_CREATE_USER = values.BooleanValue(
        default=True,
        environ_name="OIDC_CREATE_USER",
    )
    OIDC_RP_SIGN_ALGO = values.Value(
        "RS256", environ_name="OIDC_RP_SIGN_ALGO", environ_prefix=None
    )
    OIDC_RP_CLIENT_ID = values.Value(
        "main", environ_name="OIDC_RP_CLIENT_ID", environ_prefix=None
    )
    OIDC_RP_CLIENT_SECRET = values.Value(
        None,
        environ_name="OIDC_RP_CLIENT_SECRET",
        environ_prefix=None,
    )
    OIDC_OP_JWKS_ENDPOINT = values.Value(
        environ_name="OIDC_OP_JWKS_ENDPOINT", environ_prefix=None
    )
    OIDC_OP_AUTHORIZATION_ENDPOINT = values.Value(
        environ_name="OIDC_OP_AUTHORIZATION_ENDPOINT", environ_prefix=None
    )
    OIDC_OP_TOKEN_ENDPOINT = values.Value(
        None, environ_name="OIDC_OP_TOKEN_ENDPOINT", environ_prefix=None
    )
    OIDC_OP_USER_ENDPOINT = values.Value(
        None, environ_name="OIDC_OP_USER_ENDPOINT", environ_prefix=None
    )
    OIDC_OP_LOGOUT_ENDPOINT = values.Value(
        None, environ_name="OIDC_OP_LOGOUT_ENDPOINT", environ_prefix=None
    )
    OIDC_AUTH_REQUEST_EXTRA_PARAMS = values.DictValue(
        {}, environ_name="OIDC_AUTH_REQUEST_EXTRA_PARAMS", environ_prefix=None
    )
    OIDC_RP_SCOPES = values.Value(
        "openid email", environ_name="OIDC_RP_SCOPES", environ_prefix=None
    )
    LOGIN_REDIRECT_URL = values.Value(
        None, environ_name="LOGIN_REDIRECT_URL", environ_prefix=None
    )
    LOGIN_REDIRECT_URL_FAILURE = values.Value(
        None, environ_name="LOGIN_REDIRECT_URL_FAILURE", environ_prefix=None
    )
    # Frontend path users with an account pending admin validation (restricted
    # mode, is_active=False) are redirected to, appended to LOGIN_REDIRECT_URL_FAILURE's
    # domain instead of redirecting to its path (the logged-out homepage).
    LOGIN_REDIRECT_PENDING_VALIDATION_PATH = values.Value(
        "/account-pending",
        environ_name="LOGIN_REDIRECT_PENDING_VALIDATION_PATH",
        environ_prefix=None,
    )
    OIDC_CALLBACK_CLASS = "core.authentication.views.OIDCAuthenticationCallbackView"
    LOGOUT_REDIRECT_URL = values.Value(
        None, environ_name="LOGOUT_REDIRECT_URL", environ_prefix=None
    )
    OIDC_USE_NONCE = values.BooleanValue(
        default=True, environ_name="OIDC_USE_NONCE", environ_prefix=None
    )
    OIDC_REDIRECT_REQUIRE_HTTPS = values.BooleanValue(
        default=False, environ_name="OIDC_REDIRECT_REQUIRE_HTTPS", environ_prefix=None
    )
    OIDC_REDIRECT_ALLOWED_HOSTS = values.ListValue(
        default=[], environ_name="OIDC_REDIRECT_ALLOWED_HOSTS", environ_prefix=None
    )
    OIDC_STORE_ID_TOKEN = values.BooleanValue(
        default=True, environ_name="OIDC_STORE_ID_TOKEN", environ_prefix=None
    )
    ALLOW_LOGOUT_GET_METHOD = values.BooleanValue(
        default=True, environ_name="ALLOW_LOGOUT_GET_METHOD", environ_prefix=None
    )
