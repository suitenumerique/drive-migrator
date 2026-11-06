"""Installed apps, middleware, templates and authentication backends."""

import os

from .paths import BASE_DIR


class ApplicationMixin:
    """Django app registry: templates, middleware, apps and auth backends."""

    # Templates
    TEMPLATES = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [os.path.join(BASE_DIR, "templates")],
            "OPTIONS": {
                "context_processors": [
                    "django.contrib.auth.context_processors.auth",
                    "django.contrib.messages.context_processors.messages",
                    "django.template.context_processors.csrf",
                    "django.template.context_processors.debug",
                    "django.template.context_processors.i18n",
                    "django.template.context_processors.media",
                    "django.template.context_processors.request",
                    "django.template.context_processors.tz",
                ],
                "loaders": [
                    "django.template.loaders.filesystem.Loader",
                    "django.template.loaders.app_directories.Loader",
                ],
            },
        },
    ]

    MIDDLEWARE = [
        "django.middleware.security.SecurityMiddleware",
        "whitenoise.middleware.WhiteNoiseMiddleware",
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.middleware.locale.LocaleMiddleware",
        "django.middleware.clickjacking.XFrameOptionsMiddleware",
        "corsheaders.middleware.CorsMiddleware",
        "django.middleware.common.CommonMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "django.contrib.messages.middleware.MessageMiddleware",
        "dockerflow.django.middleware.DockerflowMiddleware",
        "hijack.middleware.HijackUserMiddleware",
    ]

    AUTHENTICATION_BACKENDS = [
        "django.contrib.auth.backends.ModelBackend",
        "core.authentication.backends.OIDCAuthenticationBackend",
    ]

    # Django applications from the highest priority to the lowest
    INSTALLED_APPS = [
        # Makes the superuser command not overridden by auth.
        "demo",
        # Django
        "django.contrib.admin",
        "django.contrib.auth",
        "django.contrib.contenttypes",
        "django.contrib.postgres",
        "django.contrib.sessions",
        "django.contrib.sites",
        "django.contrib.messages",
        "django.contrib.staticfiles",
        # app
        "core",
        # Third party apps
        "drf_spectacular",
        "corsheaders",
        "dockerflow.django",
        "rest_framework",
        "django_filters",
        # Third Party apps
        "hijack",
        "hijack.contrib.admin",
        # OIDC third party
        "mozilla_django_oidc",
        "django_celery_results",
    ]
