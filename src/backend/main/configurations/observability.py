"""Observability settings: Sentry, PostHog and easy-thumbnails.

The actual Sentry/PostHog client initialization happens in ``Base.post_setup``
once these values have been resolved.
"""

from configurations import values


class ObservabilityMixin:
    """Error reporting (Sentry), product analytics (PostHog) and thumbnails."""

    # Sentry
    SENTRY_DSN = values.Value(environ_name="SENTRY_DSN", environ_prefix=None)

    # Posthog
    POSTHOG_KEY = values.Value(environ_name="POSTHOG_KEY", environ_prefix=None)
    POSTHOG_HOST = values.Value(
        "https://eu.i.posthog.com", environ_name="POSTHOG_HOST", environ_prefix=None
    )

    # Easy thumbnails
    THUMBNAIL_EXTENSION = "webp"
    THUMBNAIL_TRANSPARENCY_EXTENSION = "webp"
    THUMBNAIL_ALIASES = {}
