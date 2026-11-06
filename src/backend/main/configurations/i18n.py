"""Internationalization and localization settings."""

import os

from django.utils.translation import gettext_lazy as _

from configurations import values

from .paths import BASE_DIR


class I18nMixin:
    """Languages, locale paths and timezone configuration."""

    # Internationalization
    # https://docs.djangoproject.com/en/3.1/topics/i18n/

    # Languages
    LANGUAGE_CODE = values.Value("en-us")

    # Careful! Languages should be ordered by priority, as this tuple is used to get
    # fallback/default languages throughout the app.
    LANGUAGES = values.SingleNestedTupleValue(
        (
            ("en-us", _("English")),
            ("fr-fr", _("French")),
        )
    )

    LOCALE_PATHS = (os.path.join(BASE_DIR, "locale"),)

    TIME_ZONE = "UTC"
    USE_I18N = True
    USE_TZ = True
