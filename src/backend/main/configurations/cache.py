"""Cache and session storage settings."""

from configurations import values


class CacheMixin:
    """Redis cache backend and the cache-backed session configuration."""

    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": values.Value(
                "redis://redis:6379/1",
                environ_name="REDIS_URL",
                environ_prefix=None,
            ),
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
            },
        },
    }

    # Session
    SESSION_ENGINE = "django.contrib.sessions.backends.cache"
    SESSION_CACHE_ALIAS = "default"
    SESSION_COOKIE_AGE = 60 * 60 * 12
