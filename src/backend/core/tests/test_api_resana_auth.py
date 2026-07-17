"""Tests for the Resana auth endpoints (connect + status)."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.utils import timezone

import jwt
import pytest
from cryptography.fernet import Fernet
from rest_framework.test import APIClient

from core.api.views.resana_auth import (
    KeycloakOtpRequired,
    OtpChallenge,
    _keycloak_login,
    _keycloak_submit_otp,
    _load_challenge,
    _otp_cache_key,
    _store_challenge,
)
from core.encryption import encrypt_token
from core.factories import UserFactory

pytestmark = pytest.mark.django_db

CONNECT_URL = "/api/v1.0/resana/auth/connect"
OTP_URL = "/api/v1.0/resana/auth/otp"
STATUS_URL = "/api/v1.0/resana/auth/status"


def _auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


# ---------------------------------------------------------------------------
# Authentication guard
# ---------------------------------------------------------------------------


def test_connect_requires_authentication():
    response = APIClient().post(CONNECT_URL, {"password": "pw"})
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


def test_connect_returns_400_when_password_missing():
    user = UserFactory()
    response = _auth_client(user).post(CONNECT_URL, {})
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# POST /resana/auth/connect — Keycloak flow
# ---------------------------------------------------------------------------


def test_connect_stores_tokens_on_success(settings):
    settings.RESANA_KEYCLOAK_ENDPOINT = "https://kc.example.com/realms/TEST"
    settings.RESANA_AUTH_ENDPOINT = "https://resana.example.com/public/auth/login"
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()

    user = UserFactory(email="u@example.com")

    with patch("core.api.views.resana_auth._keycloak_login") as mock_login:
        mock_login.return_value = (_make_access_jwt(), "fresh-refresh")
        response = _auth_client(user).post(CONNECT_URL, {"password": "s3cr3t"})

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


def test_connect_ignores_email_from_request_body(settings):
    """The Resana login always uses the authenticated user's app email,
    never a value supplied in the request body."""
    settings.RESANA_KEYCLOAK_ENDPOINT = "https://kc.example.com/realms/TEST"
    settings.RESANA_AUTH_ENDPOINT = "https://resana.example.com/public/auth/login"
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()

    user = UserFactory(email="real-user@example.com")

    with patch("core.api.views.resana_auth._keycloak_login") as mock_login:
        mock_login.return_value = (_make_access_jwt(), "ref")
        response = _auth_client(user).post(
            CONNECT_URL,
            {"email": "spoofed@example.com", "password": "s3cr3t"},
        )

    assert response.status_code == 200
    mock_login.assert_called_once_with(
        "real-user@example.com",
        "s3cr3t",
        keycloak_endpoint="https://kc.example.com/realms/TEST",
        resana_auth_endpoint="https://resana.example.com/public/auth/login",
    )


def test_connect_returns_otp_required_when_mfa_challenged(settings):
    """When Keycloak challenges with MFA, connect must report otp_required
    instead of storing tokens, and stash the (encrypted) challenge for the
    OTP step."""
    settings.RESANA_KEYCLOAK_ENDPOINT = "https://kc.example.com/realms/TEST"
    settings.RESANA_AUTH_ENDPOINT = "https://resana.example.com/public/auth/login"
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()

    user = UserFactory()
    challenge = _make_challenge()

    with patch("core.api.views.resana_auth._keycloak_login") as mock_login:
        mock_login.side_effect = KeycloakOtpRequired(challenge)
        response = _auth_client(user).post(CONNECT_URL, {"password": "s3cr3t"})

    assert response.status_code == 200
    assert response.data["otp_required"] is True

    user.refresh_from_db()
    assert user.resana_access_token == ""

    assert _load_challenge(user.id) == challenge

    raw_cached_value = cache.get(_otp_cache_key(user.id))
    assert isinstance(raw_cached_value, str)
    assert challenge.login_action not in raw_cached_value
    for cookie_value in challenge.cookies.values():
        assert cookie_value not in raw_cached_value


def test_connect_returns_401_on_bad_credentials(settings):
    settings.RESANA_KEYCLOAK_ENDPOINT = "https://kc.example.com/realms/TEST"
    settings.RESANA_AUTH_ENDPOINT = "https://resana.example.com/public/auth/login"

    user = UserFactory()

    with patch("core.api.views.resana_auth._keycloak_login") as mock_login:
        mock_login.side_effect = ValueError("Auth failed")
        response = _auth_client(user).post(CONNECT_URL, {"password": "wrong"})

    assert response.status_code == 401


def test_connect_returns_status_connected_after_success(settings):
    settings.RESANA_KEYCLOAK_ENDPOINT = "https://kc.example.com/realms/TEST"
    settings.RESANA_AUTH_ENDPOINT = "https://resana.example.com/public/auth/login"
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()

    user = UserFactory()
    client = _auth_client(user)

    with patch("core.api.views.resana_auth._keycloak_login") as mock_login:
        mock_login.return_value = (_make_access_jwt(), "ref")
        client.post(CONNECT_URL, {"password": "pw"})

    response = client.get(STATUS_URL)
    assert response.data["connected"] is True


# ---------------------------------------------------------------------------
# POST /resana/auth/otp
# ---------------------------------------------------------------------------


def test_otp_requires_authentication():
    """Same auth guard as the other Resana auth endpoints."""
    response = APIClient().post(OTP_URL, {"code": "123456"})
    assert response.status_code == 401


def test_otp_returns_400_when_code_missing_or_malformed():
    """The OTP code must be exactly 6 digits."""
    user = UserFactory()
    client = _auth_client(user)

    assert client.post(OTP_URL, {}).status_code == 400
    assert client.post(OTP_URL, {"code": "abc"}).status_code == 400
    assert client.post(OTP_URL, {"code": "12345"}).status_code == 400


def test_otp_returns_400_when_no_pending_challenge():
    """Without a prior connect() call that raised otp_required, there is
    nothing to complete — the user must restart from the password step."""
    user = UserFactory()
    response = _auth_client(user).post(OTP_URL, {"code": "123456"})
    assert response.status_code == 400


def test_otp_stores_tokens_on_success(settings):
    """Given a pending challenge, a valid OTP code completes the login,
    stores the tokens, and clears the pending challenge."""
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()

    user = UserFactory()
    challenge = _make_challenge()
    _store_challenge(user.id, challenge)

    with patch("core.api.views.resana_auth._keycloak_submit_otp") as mock_submit:
        mock_submit.return_value = (_make_access_jwt(), "ref-tok")
        response = _auth_client(user).post(OTP_URL, {"code": "123456"})

    assert response.status_code == 200
    mock_submit.assert_called_once_with("123456", challenge)

    user.refresh_from_db()
    assert user.resana_access_token != ""
    assert cache.get(_otp_cache_key(user.id)) is None


def test_otp_returns_401_on_invalid_code(settings):
    """An invalid OTP code must not clear the pending challenge, so the
    user can retry without re-entering their password."""
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()

    user = UserFactory()
    challenge = _make_challenge()
    _store_challenge(user.id, challenge)

    with patch("core.api.views.resana_auth._keycloak_submit_otp") as mock_submit:
        mock_submit.side_effect = ValueError("Authentication failed")
        response = _auth_client(user).post(OTP_URL, {"code": "000000"})

    assert response.status_code == 401
    assert _load_challenge(user.id) == challenge


def test_connect_then_otp_full_flow(settings):
    """End-to-end: connect() reports otp_required, then otp() completes the
    login and status() reflects the connected state."""
    settings.RESANA_KEYCLOAK_ENDPOINT = "https://kc.example.com/realms/TEST"
    settings.RESANA_AUTH_ENDPOINT = "https://resana.example.com/public/auth/login"
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()

    user = UserFactory()
    client = _auth_client(user)
    challenge = _make_challenge()

    with patch("core.api.views.resana_auth._keycloak_login") as mock_login:
        mock_login.side_effect = KeycloakOtpRequired(challenge)
        connect_response = client.post(CONNECT_URL, {"password": "s3cr3t"})

    assert connect_response.data["otp_required"] is True

    with patch("core.api.views.resana_auth._keycloak_submit_otp") as mock_submit:
        mock_submit.return_value = (_make_access_jwt(), "ref-tok")
        otp_response = client.post(OTP_URL, {"code": "123456"})

    assert otp_response.status_code == 200

    status_response = client.get(STATUS_URL)
    assert status_response.data["connected"] is True


# ---------------------------------------------------------------------------
# _store_challenge() / _load_challenge() — encrypted cache round-trip
# ---------------------------------------------------------------------------


def test_store_and_load_challenge_round_trip(settings):
    """A challenge stored via _store_challenge must come back identical
    through _load_challenge."""
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()

    challenge = _make_challenge()
    _store_challenge(42, challenge)

    assert _load_challenge(42) == challenge


def test_store_challenge_encrypts_cached_value(settings):
    """The raw value in the cache backend must not contain the challenge's
    plaintext fields — it must be Fernet-encrypted."""
    settings.OIDC_TOKENS_ENCRYPTION_KEY = _fernet_key()

    challenge = _make_challenge()
    _store_challenge(42, challenge)

    raw_value = cache.get(_otp_cache_key(42))
    assert isinstance(raw_value, str)
    assert challenge.login_action not in raw_value
    assert challenge.selected_credential_id not in raw_value
    for cookie_value in challenge.cookies.values():
        assert cookie_value not in raw_value


def test_load_challenge_returns_none_when_absent():
    """No cached entry means no pending challenge."""
    assert _load_challenge(999999) is None


# ---------------------------------------------------------------------------
# _keycloak_login() unit tests
# ---------------------------------------------------------------------------


def test_keycloak_login_returns_tokens_on_success():
    mock_response_get = MagicMock()
    mock_response_get.text = '<form action="https://kc.example.com/login-actions/authenticate?session_code=XYZ"></form>'
    mock_response_post = MagicMock()

    with patch("core.api.views.resana_auth.requests.Session") as mock_session_cls:
        session = mock_session_cls.return_value
        session.get.return_value = mock_response_get
        session.post.return_value = mock_response_post
        session.cookies.get.side_effect = {
            "interstis_access": "acc-tok",
            "interstis_refresh": "ref-tok",
        }.get

        access, refresh = _keycloak_login(
            "user@example.com",
            "secret",
            keycloak_endpoint="https://kc.example.com/realms/TEST",
            resana_auth_endpoint="https://resana.example.com/public/auth/login",
        )

    assert access == "acc-tok"
    assert refresh == "ref-tok"


def test_keycloak_login_raises_when_form_not_found():
    mock_response_get = MagicMock()
    mock_response_get.text = "<html>no form here</html>"

    with patch("core.api.views.resana_auth.requests.Session") as mock_session_cls:
        session = mock_session_cls.return_value
        session.get.return_value = mock_response_get

        with pytest.raises(ValueError, match="login form not found"):
            _keycloak_login(
                "user@example.com",
                "secret",
                keycloak_endpoint="https://kc.example.com/realms/TEST",
                resana_auth_endpoint="https://resana.example.com/public/auth/login",
            )


def test_keycloak_login_raises_when_no_cookie():
    mock_response_get = MagicMock()
    mock_response_get.text = (
        '<form action="https://kc.example.com/authenticate"></form>'
    )
    mock_response_post = MagicMock()
    mock_response_post.text = "<html>invalid credentials</html>"

    with patch("core.api.views.resana_auth.requests.Session") as mock_session_cls:
        session = mock_session_cls.return_value
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
    mock_response_get = MagicMock()
    mock_response_get.text = (
        '<form action="https://kc.example.com/auth?session_code=X&amp;tab_id=Y"></form>'
    )
    mock_response_post = MagicMock()

    with patch("core.api.views.resana_auth.requests.Session") as mock_session_cls:
        session = mock_session_cls.return_value
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
# _keycloak_login() — MFA / OTP challenge detection
# ---------------------------------------------------------------------------


def _its_portail_html(
    login_action="https://kc.example.com/login-actions/authenticate?session_code=NEW&execution=E1",
    otp_input_only="true",
    selected_credentials="abc-123",
):
    """Build the its-portail OTP challenge markup Keycloak returns when MFA is required."""
    return (
        "<its-portail "
        f'login_action="{login_action}" '
        'mail="u@example.com" '
        'otp_qr_code="" '
        'otp_code="" '
        'otp_secret="" '
        f'otp_input_only="{otp_input_only}" '
        f'selected_credentials="{selected_credentials}">'
        "</its-portail>"
    )


def test_keycloak_login_raises_otp_required_when_its_portail_present():
    """When Keycloak challenges with MFA, the login/password step must
    raise KeycloakOtpRequired carrying the parsed challenge instead of
    failing with a generic ValueError."""
    mock_response_get = MagicMock()
    mock_response_get.text = (
        '<form action="https://kc.example.com/authenticate?session_code=XYZ"></form>'
    )
    mock_response_post = MagicMock()
    mock_response_post.text = _its_portail_html()

    with patch("core.api.views.resana_auth.requests.Session") as mock_session_cls:
        session = mock_session_cls.return_value
        session.get.return_value = mock_response_get
        session.post.return_value = mock_response_post
        session.cookies.get.return_value = None
        session.cookies.get_dict.return_value = {"AUTH_SESSION_ID": "xyz"}

        with pytest.raises(KeycloakOtpRequired) as exc_info:
            _keycloak_login(
                "u@example.com",
                "pw",
                keycloak_endpoint="https://kc.example.com/realms/TEST",
                resana_auth_endpoint="https://resana.example.com/public/auth/login",
            )

    challenge = exc_info.value.challenge
    assert (
        challenge.login_action
        == "https://kc.example.com/login-actions/authenticate?session_code=NEW&execution=E1"
    )
    assert challenge.otp_field_name == "otp"
    assert challenge.selected_credential_id == "abc-123"
    assert challenge.cookies == {"AUTH_SESSION_ID": "xyz"}


def test_keycloak_login_otp_field_name_is_totp_when_not_input_only():
    """otp_input_only="false" means the account needs the QR-setup flow,
    whose form field is named "totp" instead of "otp"."""
    mock_response_get = MagicMock()
    mock_response_get.text = (
        '<form action="https://kc.example.com/authenticate?session_code=XYZ"></form>'
    )
    mock_response_post = MagicMock()
    mock_response_post.text = _its_portail_html(otp_input_only="false")

    with patch("core.api.views.resana_auth.requests.Session") as mock_session_cls:
        session = mock_session_cls.return_value
        session.get.return_value = mock_response_get
        session.post.return_value = mock_response_post
        session.cookies.get.return_value = None
        session.cookies.get_dict.return_value = {}

        with pytest.raises(KeycloakOtpRequired) as exc_info:
            _keycloak_login(
                "u@example.com",
                "pw",
                keycloak_endpoint="https://kc.example.com/realms/TEST",
                resana_auth_endpoint="https://resana.example.com/public/auth/login",
            )

    assert exc_info.value.challenge.otp_field_name == "totp"


def test_keycloak_login_decodes_html_entities_in_login_action():
    """&amp; in the its-portail login_action attribute is decoded, same as
    the regular form action."""
    mock_response_get = MagicMock()
    mock_response_get.text = (
        '<form action="https://kc.example.com/authenticate?session_code=XYZ"></form>'
    )
    mock_response_post = MagicMock()
    mock_response_post.text = _its_portail_html(
        login_action="https://kc.example.com/authenticate?session_code=X&amp;execution=Y"
    )

    with patch("core.api.views.resana_auth.requests.Session") as mock_session_cls:
        session = mock_session_cls.return_value
        session.get.return_value = mock_response_get
        session.post.return_value = mock_response_post
        session.cookies.get.return_value = None
        session.cookies.get_dict.return_value = {}

        with pytest.raises(KeycloakOtpRequired) as exc_info:
            _keycloak_login(
                "u@example.com",
                "pw",
                keycloak_endpoint="https://kc.example.com/realms/TEST",
                resana_auth_endpoint="https://resana.example.com/public/auth/login",
            )

    login_action = exc_info.value.challenge.login_action
    assert "&amp;" not in login_action
    assert "session_code=X&execution=Y" in login_action


# ---------------------------------------------------------------------------
# _keycloak_submit_otp() unit tests
# ---------------------------------------------------------------------------


def _make_challenge(**overrides):
    defaults = {
        "login_action": "https://kc.example.com/login-actions/authenticate?session_code=NEW&execution=E1",
        "otp_field_name": "otp",
        "selected_credential_id": "abc-123",
        "cookies": {"AUTH_SESSION_ID": "xyz"},
    }
    defaults.update(overrides)
    return OtpChallenge(**defaults)


def test_keycloak_submit_otp_returns_tokens_on_success():
    """Posting the OTP code to the challenge's login_action, on a session
    restored from the saved cookies, must yield the Resana tokens."""
    challenge = _make_challenge()
    mock_response = MagicMock()

    with patch("core.api.views.resana_auth.requests.Session") as mock_session_cls:
        session = mock_session_cls.return_value
        session.post.return_value = mock_response
        session.cookies.get.side_effect = {
            "interstis_access": "acc-tok",
            "interstis_refresh": "ref-tok",
        }.get

        access, refresh = _keycloak_submit_otp("123456", challenge)

    session.cookies.update.assert_called_once_with(challenge.cookies)
    session.post.assert_called_once_with(
        challenge.login_action,
        data={
            "otp": "123456",
            "selectedCredentialId": "abc-123",
            "userLabel": "Mon OTP",
        },
        allow_redirects=True,
        timeout=15,
    )
    assert access == "acc-tok"
    assert refresh == "ref-tok"


def test_keycloak_submit_otp_uses_totp_field_name():
    """The posted field name must match the challenge's otp_field_name."""
    challenge = _make_challenge(otp_field_name="totp")
    mock_response = MagicMock()

    with patch("core.api.views.resana_auth.requests.Session") as mock_session_cls:
        session = mock_session_cls.return_value
        session.post.return_value = mock_response
        session.cookies.get.side_effect = lambda k: (
            "tok" if k == "interstis_access" else None
        )

        _keycloak_submit_otp("654321", challenge)

    posted_data = session.post.call_args.kwargs["data"]
    assert posted_data == {
        "totp": "654321",
        "selectedCredentialId": "abc-123",
        "userLabel": "Mon OTP",
    }


def test_keycloak_submit_otp_raises_on_invalid_code():
    """No interstis_access cookie in the response means the OTP code was rejected."""
    challenge = _make_challenge()
    mock_response = MagicMock()

    with patch("core.api.views.resana_auth.requests.Session") as mock_session_cls:
        session = mock_session_cls.return_value
        session.post.return_value = mock_response
        session.cookies.get.return_value = None

        with pytest.raises(ValueError, match="Authentication failed"):
            _keycloak_submit_otp("000000", challenge)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fernet_key() -> str:
    return Fernet.generate_key().decode()


def _make_access_jwt(exp_delta=timedelta(hours=3)) -> str:
    """Build a fake Resana access token JWT carrying an `exp` claim.

    store_tokens() reads its own expiry from this claim, so any mocked
    access token used in these tests must be shaped like a real one.
    """
    exp = timezone.now() + exp_delta
    return jwt.encode(
        {"exp": int(exp.timestamp())},
        "test-secret-long-enough-for-hs256",
        algorithm="HS256",
    )
