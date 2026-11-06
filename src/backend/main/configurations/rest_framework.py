"""Django REST Framework and drf-spectacular (OpenAPI schema) settings."""

from configurations import values


class DRFMixin:
    """REST framework defaults, the nested multipart parser and the API schema."""

    DRF_NESTED_MULTIPART_PARSER = {
        # output of parser is converted to querydict
        # if is set to False, dict python is returned
        "querydict": False,
    }

    REST_FRAMEWORK = {
        "DEFAULT_AUTHENTICATION_CLASSES": (
            "mozilla_django_oidc.contrib.drf.OIDCAuthentication",
            "rest_framework.authentication.SessionAuthentication",
        ),
        "DEFAULT_PARSER_CLASSES": [
            "rest_framework.parsers.JSONParser",
            "nested_multipart_parser.drf.DrfNestedParser",
        ],
        "EXCEPTION_HANDLER": "core.api.exception_handler",
        "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
        "PAGE_SIZE": 1000,
        "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.URLPathVersioning",
        "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    }

    SPECTACULAR_SETTINGS = {
        "TITLE": "Impress API",
        "DESCRIPTION": "This is the API schema.",
        "VERSION": "1.0.0",
        "SERVE_INCLUDE_SCHEMA": False,
        "ENABLE_DJANGO_DEPLOY_CHECK": values.BooleanValue(
            default=False,
            environ_name="SPECTACULAR_SETTINGS_ENABLE_DJANGO_DEPLOY_CHECK",
        ),
        "COMPONENT_SPLIT_REQUEST": True,
        # OTHER SETTINGS
        "SWAGGER_UI_DIST": "SIDECAR",  # shorthand to use the sidecar instead
        "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
        "REDOC_DIST": "SIDECAR",
    }
