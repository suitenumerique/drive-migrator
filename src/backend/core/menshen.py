"""Menshen (RFC 8693) token exchange client, shared by destination backends."""

import functools

from django.conf import settings

from menshen_client import Configuration, TokenExchangeClient


@functools.cache
def get_menshen_client() -> TokenExchangeClient:
    """One long-lived client per process, as the Menshen client recommends."""
    return TokenExchangeClient(
        config=Configuration(
            client_id=settings.MENSHEN_CLIENT_ID,
            client_secret=settings.MENSHEN_CLIENT_SECRET,
            server_root_url=settings.MENSHEN_SERVER_ROOT_URL,
            requests_timeout=settings.MENSHEN_REQUESTS_TIMEOUT,
        )
    )
