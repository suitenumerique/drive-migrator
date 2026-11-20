"""Tests for the shared Menshen (RFC 8693) token exchange client."""

from core.menshen import get_menshen_client


def test_menshen_client_is_configured_from_the_settings(settings):
    """The client authenticates to the configured Menshen server."""
    settings.MENSHEN_SERVER_ROOT_URL = "https://menshen.example.com"
    settings.MENSHEN_CLIENT_ID = "migrator"
    settings.MENSHEN_CLIENT_SECRET = "migrator-secret"
    settings.MENSHEN_REQUESTS_TIMEOUT = 12.0

    get_menshen_client.cache_clear()
    try:
        config = get_menshen_client().config
    finally:
        get_menshen_client.cache_clear()

    assert config.client_id == "migrator"
    assert config.client_secret == "migrator-secret"
    assert config.token_url == "https://menshen.example.com/auth/token/exchange/"
    assert config.requests_timeout == 12.0
