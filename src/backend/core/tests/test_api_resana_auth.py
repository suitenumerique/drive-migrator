"""Tests for the Resana auth endpoints (connect + status)."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from core.encryption import encrypt_token
from core.factories import UserFactory

pytestmark = pytest.mark.django_db

CONNECT_URL = "/api/v1.0/resana/auth/connect"
STATUS_URL = "/api/v1.0/resana/auth/status"


def _auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


# ---------------------------------------------------------------------------
# Authentication guard
# ---------------------------------------------------------------------------


def test_connect_requires_authentication():
    response = APIClient().post(
        CONNECT_URL, {"email": "u@example.com", "password": "pw"}
    )
    assert response.status_code == 401


def test_status_requires_authentication():
    response = APIClient().get(STATUS_URL)
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /resana/auth/status
# ---------------------------------------------------------------------------


def test_status_returns_not_connected_when_no_token():
    user = UserFactory()
    response = _auth_client(user).get(STATUS_URL)
    assert response.status_code == 200
    assert response.data["connected"] is False
    assert response.data["expires_at"] is None


def test_status_returns_connected_when_token_present():
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
# POST /resana/auth/connect — validation
# ---------------------------------------------------------------------------


def test_connect_returns_400_when_email_missing():
    user = UserFactory()
    response = _auth_client(user).post(CONNECT_URL, {"password": "pw"})
    assert response.status_code == 400


def test_connect_returns_400_when_password_missing():
    user = UserFactory()
    response = _auth_client(user).post(CONNECT_URL, {"email": "u@example.com"})
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# POST /resana/auth/connect — Keycloak flow
# ---------------------------------------------------------------------------


def test_connect_stores_tokens_on_success(settings):
    settings.RESANA_KEYCLOAK_ENDPOINT = "https://kc.example.com/realms/TEST"
    settings.RESANA_AUTH_ENDPOINT = "https://resana.example.com/public/auth/login"
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()

    user = UserFactory()

    with patch("core.api.views.resana_auth._keycloak_login") as mock_login:
        mock_login.return_value = ("fresh-access", "fresh-refresh")
        response = _auth_client(user).post(
            CONNECT_URL, {"email": "u@example.com", "password": "s3cr3t"}
        )

    assert response.status_code == 200
    mock_login.assert_called_once_with(
        "u@example.com",
        "s3cr3t",
        keycloak_endpoint="https://kc.example.com/realms/TEST",
        resana_auth_endpoint="https://resana.example.com/public/auth/login",
    )
    user.refresh_from_db()
    assert user.resana_access_token != ""
    assert user.resana_refresh_token != ""
    assert user.resana_token_expires_at is not None


def test_connect_returns_401_on_bad_credentials(settings):
    settings.RESANA_KEYCLOAK_ENDPOINT = "https://kc.example.com/realms/TEST"
    settings.RESANA_AUTH_ENDPOINT = "https://resana.example.com/public/auth/login"

    user = UserFactory()

    with patch("core.api.views.resana_auth._keycloak_login") as mock_login:
        mock_login.side_effect = ValueError("Auth failed")
        response = _auth_client(user).post(
            CONNECT_URL, {"email": "u@example.com", "password": "wrong"}
        )

    assert response.status_code == 401


def test_connect_returns_status_connected_after_success(settings):
    settings.RESANA_KEYCLOAK_ENDPOINT = "https://kc.example.com/realms/TEST"
    settings.RESANA_AUTH_ENDPOINT = "https://resana.example.com/public/auth/login"
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()

    user = UserFactory()
    client = _auth_client(user)

    with patch("core.api.views.resana_auth._keycloak_login") as mock_login:
        mock_login.return_value = ("acc", "ref")
        client.post(CONNECT_URL, {"email": "u@example.com", "password": "pw"})

    response = client.get(STATUS_URL)
    assert response.data["connected"] is True


# ---------------------------------------------------------------------------
# _keycloak_login() unit tests
# ---------------------------------------------------------------------------


def test_keycloak_login_returns_tokens_on_success():
    from core.api.views.resana_auth import _keycloak_login

    mock_response_get = MagicMock()
    mock_response_get.text = '<form action="https://kc.example.com/login-actions/authenticate?session_code=XYZ"></form>'
    mock_response_post = MagicMock()

    with patch("core.api.views.resana_auth.requests.Session") as MockSession:
        session = MockSession.return_value
        session.get.return_value = mock_response_get
        session.post.return_value = mock_response_post
        session.cookies.get.side_effect = lambda k: {
            "interstis_access": "acc-tok",
            "interstis_refresh": "ref-tok",
        }.get(k)

        access, refresh = _keycloak_login(
            "user@example.com",
            "secret",
            keycloak_endpoint="https://kc.example.com/realms/TEST",
            resana_auth_endpoint="https://resana.example.com/public/auth/login",
        )

    assert access == "acc-tok"
    assert refresh == "ref-tok"


def test_keycloak_login_raises_when_form_not_found():
    from core.api.views.resana_auth import _keycloak_login

    mock_response_get = MagicMock()
    mock_response_get.text = "<html>no form here</html>"

    with patch("core.api.views.resana_auth.requests.Session") as MockSession:
        session = MockSession.return_value
        session.get.return_value = mock_response_get

        with pytest.raises(ValueError, match="login form not found"):
            _keycloak_login(
                "user@example.com",
                "secret",
                keycloak_endpoint="https://kc.example.com/realms/TEST",
                resana_auth_endpoint="https://resana.example.com/public/auth/login",
            )


def test_keycloak_login_raises_when_no_cookie():
    from core.api.views.resana_auth import _keycloak_login

    mock_response_get = MagicMock()
    mock_response_get.text = (
        '<form action="https://kc.example.com/authenticate"></form>'
    )
    mock_response_post = MagicMock()

    with patch("core.api.views.resana_auth.requests.Session") as MockSession:
        session = MockSession.return_value
        session.get.return_value = mock_response_get
        session.post.return_value = mock_response_post
        session.cookies.get.return_value = None

        with pytest.raises(ValueError, match="Authentication failed"):
            _keycloak_login(
                "user@example.com",
                "wrong",
                keycloak_endpoint="https://kc.example.com/realms/TEST",
                resana_auth_endpoint="https://resana.example.com/public/auth/login",
            )


def test_keycloak_login_decodes_html_entities_in_form_action():
    """&amp; in form action is decoded before posting."""
    from core.api.views.resana_auth import _keycloak_login

    mock_response_get = MagicMock()
    mock_response_get.text = (
        '<form action="https://kc.example.com/auth?session_code=X&amp;tab_id=Y"></form>'
    )
    mock_response_post = MagicMock()

    with patch("core.api.views.resana_auth.requests.Session") as MockSession:
        session = MockSession.return_value
        session.get.return_value = mock_response_get
        session.post.return_value = mock_response_post
        session.cookies.get.side_effect = (
            lambda k: "tok" if k == "interstis_access" else None
        )

        _keycloak_login(
            "u@example.com",
            "pw",
            keycloak_endpoint="https://kc.example.com/realms/TEST",
            resana_auth_endpoint="https://resana.example.com/public/auth/login",
        )

    call_url = session.post.call_args[0][0]
    assert "&amp;" not in call_url
    assert "session_code=X&tab_id=Y" in call_url


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fernet_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()
