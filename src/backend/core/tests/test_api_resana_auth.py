"""Tests for the Resana auth endpoints (connect + callback + status).

The connect/callback pair drives the resana-migrator PKCE flow: connect()
returns the Keycloak authorize URL, the user authenticates in their browser
(password or ProConnect, including any MFA — never simulated server-side),
and Keycloak redirects back to callback() with an authorization code.
"""

from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.utils import timezone

import jwt
import pytest
import requests
from cryptography.fernet import Fernet
from rest_framework.test import APIClient

from core.api.views.resana_auth import (
    PendingAuth,
    _load_pending_auth,
    _store_pending_auth,
)
from core.encryption import decrypt_token, encrypt_token
from core.factories import UserFactory
from core.sources.resana.migrator_auth import MigratorClient

pytestmark = pytest.mark.django_db

CONNECT_URL = "/api/v1.0/resana/auth/connect"
CALLBACK_URL = "/api/v1.0/resana-auth/callback"
STATUS_URL = "/api/v1.0/resana/auth/status"

KEYCLOAK_BASE = "https://kc.example.com/realms/ONHEXAGONE"
CLIENT_ID = "resana-migrator"
CLIENT_SECRET = "s3cr3t"
REDIRECT_URI = "https://migrator.example.com/api/v1.0/resana-auth/callback"
WEB_ENDPOINT = "https://resana.example.com"
SUCCESS_URL = "https://migrator.example.com/resana-connected"
FAILURE_URL = "https://migrator.example.com/resana-connect-failed"


def _auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _configure_settings(settings):
    """Set the resana-migrator settings shared by most tests."""
    settings.RESANA_KEYCLOAK_ENDPOINT = KEYCLOAK_BASE
    settings.RESANA_MIGRATOR_CLIENT_ID = CLIENT_ID
    settings.RESANA_MIGRATOR_CLIENT_SECRET = CLIENT_SECRET
    settings.RESANA_MIGRATOR_REDIRECT_URI = REDIRECT_URI
    settings.RESANA_WEB_ENDPOINT = WEB_ENDPOINT
    settings.RESANA_MIGRATOR_REDIRECT_URL_SUCCESS = SUCCESS_URL
    settings.RESANA_MIGRATOR_REDIRECT_URL_FAILURE = FAILURE_URL
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()


# ---------------------------------------------------------------------------
# Authentication guard
# ---------------------------------------------------------------------------


def test_connect_requires_authentication():
    """The connect endpoint must reject anonymous requests."""
    response = APIClient().get(CONNECT_URL)
    assert response.status_code == 401


def test_callback_requires_authentication():
    """The callback endpoint must reject anonymous requests."""
    response = APIClient().get(CALLBACK_URL, {"code": "abc", "state": "xyz"})
    assert response.status_code == 401


def test_status_requires_authentication():
    """The status endpoint must reject anonymous requests."""
    response = APIClient().get(STATUS_URL)
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /resana/auth/status
# ---------------------------------------------------------------------------


def test_status_returns_not_connected_when_no_token():
    """A user who never connected has no access token and no expiry."""
    user = UserFactory()
    response = _auth_client(user).get(STATUS_URL)
    assert response.status_code == 200
    assert response.data["connected"] is False
    assert response.data["expires_at"] is None


def test_status_returns_connected_when_token_present():
    """A user with a stored access token and expiry is reported as connected."""
    expires = timezone.now() + timedelta(hours=2)
    user = UserFactory(
        resana_access_token=encrypt_token("some-token"),
        resana_token_expires_at=expires,
    )
    response = _auth_client(user).get(STATUS_URL)
    assert response.status_code == 200
    assert response.data["connected"] is True
    assert response.data["expires_at"] is not None


# ---------------------------------------------------------------------------
# GET /resana/auth/connect
# ---------------------------------------------------------------------------


def test_connect_returns_an_authorize_url_targeting_keycloak(settings):
    """The response must point the browser at the Keycloak authorization endpoint."""
    _configure_settings(settings)
    user = UserFactory()

    response = _auth_client(user).get(CONNECT_URL)

    assert response.status_code == 200
    assert response.data["authorize_url"].startswith(
        f"{KEYCLOAK_BASE}/protocol/openid-connect/auth?"
    )


def test_connect_authorize_url_carries_pkce_and_client_params(settings):
    """The authorize URL must use our dedicated client_id, redirect_uri, and PKCE S256."""
    _configure_settings(settings)
    user = UserFactory()

    response = _auth_client(user).get(CONNECT_URL)

    params = parse_qs(urlparse(response.data["authorize_url"]).query)
    assert params["client_id"] == [CLIENT_ID]
    assert params["redirect_uri"] == [REDIRECT_URI]
    assert params["response_type"] == ["code"]
    assert params["code_challenge_method"] == ["S256"]
    assert "offline_access" in params["scope"][0]


def test_connect_stores_a_pending_auth_matching_the_returned_state(settings):
    """The state embedded in the authorize URL must resolve to a stored pending auth."""
    _configure_settings(settings)
    user = UserFactory()

    response = _auth_client(user).get(CONNECT_URL)

    params = parse_qs(urlparse(response.data["authorize_url"]).query)
    pending = _load_pending_auth(user.id)
    assert pending is not None
    assert pending.state == params["state"][0]
    assert pending.nonce == params["nonce"][0]


def test_connect_generates_a_fresh_state_on_each_call(settings):
    """Two connect() calls must not reuse the same state/verifier."""
    _configure_settings(settings)
    user = UserFactory()
    client = _auth_client(user)

    first = client.get(CONNECT_URL)
    second = client.get(CONNECT_URL)

    first_state = parse_qs(urlparse(first.data["authorize_url"]).query)["state"][0]
    second_state = parse_qs(urlparse(second.data["authorize_url"]).query)["state"][0]
    assert first_state != second_state


# ---------------------------------------------------------------------------
# GET /resana/auth/callback — validation
# ---------------------------------------------------------------------------


def test_callback_redirects_to_failure_when_provider_reports_an_error(settings):
    """Keycloak can redirect back with ?error=... instead of a code (e.g. access_denied)."""
    _configure_settings(settings)
    user = UserFactory()

    response = _auth_client(user).get(CALLBACK_URL, {"error": "access_denied"})

    assert response.status_code == 302
    assert response.url.startswith(FAILURE_URL)


def test_callback_failure_redirect_preserves_the_configured_query_string(settings):
    """The failure URL may already carry a query string; ?error= must be appended, not stacked."""
    _configure_settings(settings)
    settings.RESANA_MIGRATOR_REDIRECT_URL_FAILURE = f"{FAILURE_URL}?resana_error=1"
    user = UserFactory()

    response = _auth_client(user).get(CALLBACK_URL, {"error": "access_denied"})

    parsed = urlparse(response.url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == FAILURE_URL
    assert parse_qs(parsed.query) == {
        "resana_error": ["1"],
        "error": ["access_denied"],
    }


def test_callback_failure_redirect_url_encodes_the_provider_error(settings):
    """A provider-controlled error string must not inject extra query parameters."""
    _configure_settings(settings)
    user = UserFactory()

    response = _auth_client(user).get(CALLBACK_URL, {"error": "x&resana_connected=1"})

    assert parse_qs(urlparse(response.url).query) == {"error": ["x&resana_connected=1"]}


def test_callback_redirects_to_failure_when_code_is_missing(settings):
    """A callback without a code cannot proceed to the token exchange."""
    _configure_settings(settings)
    user = UserFactory()

    response = _auth_client(user).get(CALLBACK_URL, {"state": "xyz"})

    assert response.status_code == 302
    assert response.url.startswith(FAILURE_URL)


def test_callback_redirects_to_failure_when_there_is_no_pending_auth(settings):
    """A callback with no prior connect() call has nothing to validate the state against."""
    _configure_settings(settings)
    user = UserFactory()

    response = _auth_client(user).get(
        CALLBACK_URL, {"code": "the-code", "state": "unknown"}
    )

    assert response.status_code == 302
    assert response.url.startswith(FAILURE_URL)


def test_callback_redirects_to_failure_when_state_does_not_match(settings):
    """A state mismatch must be rejected, it may indicate a CSRF attempt."""
    _configure_settings(settings)
    user = UserFactory()
    _store_pending_auth(
        user.id,
        PendingAuth(state="expected-state", nonce="nonce", code_verifier="verifier"),
    )

    response = _auth_client(user).get(
        CALLBACK_URL, {"code": "the-code", "state": "wrong-state"}
    )

    assert response.status_code == 302
    assert response.url.startswith(FAILURE_URL)


# ---------------------------------------------------------------------------
# GET /resana/auth/callback — happy path
# ---------------------------------------------------------------------------


def _make_access_jwt(exp_delta=timedelta(hours=1)) -> str:
    """Build a fake bridge access token JWT carrying an exp claim."""
    exp = timezone.now() + exp_delta
    return jwt.encode(
        {"exp": int(exp.timestamp())},
        "test-secret-long-enough-for-hs256",
        algorithm="HS256",
    )


def test_callback_exchanges_the_code_with_the_stored_verifier(settings):
    """The token exchange must reuse the PKCE code_verifier generated at connect() time."""
    _configure_settings(settings)
    user = UserFactory()
    _store_pending_auth(
        user.id,
        PendingAuth(state="the-state", nonce="the-nonce", code_verifier="the-verifier"),
    )

    with patch(
        "core.api.views.resana_auth.exchange_code_for_tokens"
    ) as mock_exchange, patch(
        "core.api.views.resana_auth.create_bridge_session"
    ) as mock_bridge:
        mock_exchange.return_value = {
            "access_token": _make_access_jwt(),
            "refresh_token": "offline-tok",
        }
        mock_bridge.return_value = {
            "plateformeSessionId": "sess-id",
            "interstis_access": _make_access_jwt(),
            "csrfToken": "csrf-value",
        }
        _auth_client(user).get(CALLBACK_URL, {"code": "the-code", "state": "the-state"})

    mock_exchange.assert_called_once_with(
        KEYCLOAK_BASE,
        MigratorClient(
            client_id=CLIENT_ID, client_secret=CLIENT_SECRET, redirect_uri=REDIRECT_URI
        ),
        "the-code",
        "the-verifier",
    )


def test_callback_stores_tokens_and_redirects_to_success(settings):
    """A full successful callback must persist the bridge session and redirect to the success page."""
    _configure_settings(settings)
    user = UserFactory()
    _store_pending_auth(
        user.id,
        PendingAuth(state="the-state", nonce="the-nonce", code_verifier="the-verifier"),
    )

    bridge_access = _make_access_jwt(timedelta(minutes=5))
    with patch(
        "core.api.views.resana_auth.exchange_code_for_tokens"
    ) as mock_exchange, patch(
        "core.api.views.resana_auth.create_bridge_session"
    ) as mock_bridge:
        mock_exchange.return_value = {
            "access_token": _make_access_jwt(),
            "refresh_token": "offline-tok",
        }
        mock_bridge.return_value = {
            "plateformeSessionId": "sess-id",
            "interstis_access": bridge_access,
            "csrfToken": "csrf-value",
        }
        response = _auth_client(user).get(
            CALLBACK_URL, {"code": "the-code", "state": "the-state"}
        )

    assert response.status_code == 302
    assert response.url == SUCCESS_URL

    user.refresh_from_db()
    assert decrypt_token(user.resana_refresh_token) == "offline-tok"
    assert decrypt_token(user.resana_access_token) == bridge_access
    assert decrypt_token(user.resana_session_id) == "sess-id"
    assert decrypt_token(user.resana_csrf_token) == "csrf-value"


def test_callback_clears_the_pending_auth_so_it_cannot_be_replayed(settings):
    """The state must be single-use: a replayed callback must fail."""
    _configure_settings(settings)
    user = UserFactory()
    _store_pending_auth(
        user.id,
        PendingAuth(state="the-state", nonce="the-nonce", code_verifier="the-verifier"),
    )

    with patch(
        "core.api.views.resana_auth.exchange_code_for_tokens"
    ) as mock_exchange, patch(
        "core.api.views.resana_auth.create_bridge_session"
    ) as mock_bridge:
        mock_exchange.return_value = {
            "access_token": _make_access_jwt(),
            "refresh_token": "offline-tok",
        }
        mock_bridge.return_value = {
            "plateformeSessionId": "sess-id",
            "interstis_access": _make_access_jwt(),
            "csrfToken": "csrf-value",
        }
        _auth_client(user).get(CALLBACK_URL, {"code": "the-code", "state": "the-state"})

    assert _load_pending_auth(user.id) is None


# ---------------------------------------------------------------------------
# GET /resana/auth/callback — upstream failures
# ---------------------------------------------------------------------------


def test_callback_redirects_to_failure_when_token_exchange_fails(settings):
    """A rejected authorization code must redirect to the failure page, not crash."""
    _configure_settings(settings)
    user = UserFactory()
    _store_pending_auth(
        user.id,
        PendingAuth(state="the-state", nonce="the-nonce", code_verifier="the-verifier"),
    )

    with patch(
        "core.api.views.resana_auth.exchange_code_for_tokens",
        side_effect=requests.HTTPError("400 Bad Request"),
    ):
        response = _auth_client(user).get(
            CALLBACK_URL, {"code": "bad-code", "state": "the-state"}
        )

    assert response.status_code == 302
    assert response.url.startswith(FAILURE_URL)


@pytest.mark.parametrize(
    "exc",
    [
        requests.ConnectionError("dns failure"),
        requests.Timeout("read timed out"),
        requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0),
    ],
)
def test_callback_redirects_to_failure_when_keycloak_is_unreachable(settings, exc):
    """Network/transport errors on the token exchange must not surface as a 500."""
    _configure_settings(settings)
    user = UserFactory()
    _store_pending_auth(
        user.id,
        PendingAuth(state="the-state", nonce="the-nonce", code_verifier="the-verifier"),
    )

    with patch("core.api.views.resana_auth.exchange_code_for_tokens", side_effect=exc):
        response = _auth_client(user).get(
            CALLBACK_URL, {"code": "the-code", "state": "the-state"}
        )

    assert response.status_code == 302
    assert response.url.startswith(FAILURE_URL)


def test_callback_redirects_to_failure_when_the_bridge_is_unreachable(settings):
    """A bridge timeout must redirect to the failure page like a bridge HTTP error."""
    _configure_settings(settings)
    user = UserFactory()
    _store_pending_auth(
        user.id,
        PendingAuth(state="the-state", nonce="the-nonce", code_verifier="the-verifier"),
    )

    with patch(
        "core.api.views.resana_auth.exchange_code_for_tokens"
    ) as mock_exchange, patch(
        "core.api.views.resana_auth.create_bridge_session",
        side_effect=requests.Timeout("read timed out"),
    ):
        mock_exchange.return_value = {
            "access_token": _make_access_jwt(),
            "refresh_token": "offline-tok",
        }
        response = _auth_client(user).get(
            CALLBACK_URL, {"code": "the-code", "state": "the-state"}
        )

    assert response.status_code == 302
    assert response.url.startswith(FAILURE_URL)


def test_callback_redirects_to_failure_when_keycloak_omits_the_offline_token(settings):
    """Without offline_access granted, there is no refresh token to store for later renewal."""
    _configure_settings(settings)
    user = UserFactory()
    _store_pending_auth(
        user.id,
        PendingAuth(state="the-state", nonce="the-nonce", code_verifier="the-verifier"),
    )

    with patch("core.api.views.resana_auth.exchange_code_for_tokens") as mock_exchange:
        mock_exchange.return_value = {"access_token": _make_access_jwt()}
        response = _auth_client(user).get(
            CALLBACK_URL, {"code": "the-code", "state": "the-state"}
        )

    assert response.status_code == 302
    assert response.url.startswith(FAILURE_URL)


def test_callback_redirects_to_failure_when_the_bridge_call_fails(settings):
    """A rejected bridge call (e.g. bad audience) must redirect to the failure page."""
    _configure_settings(settings)
    user = UserFactory()
    _store_pending_auth(
        user.id,
        PendingAuth(state="the-state", nonce="the-nonce", code_verifier="the-verifier"),
    )

    with patch(
        "core.api.views.resana_auth.exchange_code_for_tokens"
    ) as mock_exchange, patch(
        "core.api.views.resana_auth.create_bridge_session",
        side_effect=requests.HTTPError("401 Unauthorized"),
    ):
        mock_exchange.return_value = {
            "access_token": _make_access_jwt(),
            "refresh_token": "offline-tok",
        }
        response = _auth_client(user).get(
            CALLBACK_URL, {"code": "the-code", "state": "the-state"}
        )

    assert response.status_code == 302
    assert response.url.startswith(FAILURE_URL)


def test_callback_redirects_to_failure_when_the_bridge_response_is_malformed(settings):
    """A 200 OK bridge response missing required fields must redirect to failure too."""
    _configure_settings(settings)
    user = UserFactory()
    _store_pending_auth(
        user.id,
        PendingAuth(state="the-state", nonce="the-nonce", code_verifier="the-verifier"),
    )

    with patch(
        "core.api.views.resana_auth.exchange_code_for_tokens"
    ) as mock_exchange, patch(
        "core.api.views.resana_auth.create_bridge_session",
        side_effect=ValueError("Bridge response missing fields: csrfToken"),
    ):
        mock_exchange.return_value = {
            "access_token": _make_access_jwt(),
            "refresh_token": "offline-tok",
        }
        response = _auth_client(user).get(
            CALLBACK_URL, {"code": "the-code", "state": "the-state"}
        )

    assert response.status_code == 302
    assert response.url.startswith(FAILURE_URL)


# ---------------------------------------------------------------------------
# _store_pending_auth() / _load_pending_auth() — encrypted cache round-trip
# ---------------------------------------------------------------------------


def test_store_and_load_pending_auth_round_trip(settings):
    """A pending auth stored via _store_pending_auth must come back identical."""
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()
    pending = PendingAuth(state="s", nonce="n", code_verifier="v")

    _store_pending_auth(42, pending)

    assert _load_pending_auth(42) == pending


def test_load_pending_auth_returns_none_when_absent():
    """No cached entry means no pending auth to validate a callback against."""
    assert _load_pending_auth(999999) is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fernet_key() -> str:
    """Return a valid Fernet key for test settings."""
    return Fernet.generate_key().decode()
