"""Tests for the resana-migrator PKCE auth flow and bridge session client."""

import base64
import hashlib
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from core.sources.resana.migrator_auth import (
    AuthorizeParams,
    MigratorClient,
    bridge_session_url,
    build_authorize_url,
    create_bridge_session,
    exchange_code_for_tokens,
    generate_pkce_pair,
    refresh_offline_token,
)

KEYCLOAK_BASE = "https://kc.example.com/realms/ONHEXAGONE"
CLIENT_ID = "resana-migrator"
CLIENT_SECRET = "s3cr3t"
REDIRECT_URI = "https://migrator.example.com/api/v1.0/resana-auth/callback"
BRIDGE_URL = "https://resana.example.com/public/auth/dinum-session"


# ---------------------------------------------------------------------------
# generate_pkce_pair()
# ---------------------------------------------------------------------------


def test_generate_pkce_pair_returns_verifier_and_challenge():
    """Both parts of the pair are distinct strings."""
    verifier, challenge = generate_pkce_pair()
    assert isinstance(verifier, str)
    assert isinstance(challenge, str)
    assert verifier != challenge


def test_generate_pkce_pair_challenge_is_sha256_of_verifier():
    """The challenge must be BASE64URL(SHA256(verifier)), per the PKCE spec."""
    verifier, challenge = generate_pkce_pair()
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    )
    expected = expected.rstrip(b"=").decode("ascii")
    assert challenge == expected


def test_generate_pkce_pair_values_are_unpadded_base64url():
    """Neither value may contain padding or the standard-base64 +/ characters."""
    verifier, challenge = generate_pkce_pair()
    for value in (verifier, challenge):
        assert "=" not in value
        assert "+" not in value
        assert "/" not in value


def test_generate_pkce_pair_returns_fresh_values_each_call():
    """Each call must produce a new random verifier, never a reused one."""
    verifier1, _ = generate_pkce_pair()
    verifier2, _ = generate_pkce_pair()
    assert verifier1 != verifier2


# ---------------------------------------------------------------------------
# build_authorize_url()
# ---------------------------------------------------------------------------


def _authorize_params(**overrides):
    """Build a minimal AuthorizeParams, with optional field overrides."""
    defaults = {
        "state": "state123",
        "nonce": "nonce123",
        "code_challenge": "challenge123",
    }
    defaults.update(overrides)
    return AuthorizeParams(**defaults)


def test_build_authorize_url_targets_the_keycloak_auth_endpoint():
    """The URL must point at the realm's authorization endpoint."""
    url = build_authorize_url(
        KEYCLOAK_BASE, CLIENT_ID, REDIRECT_URI, _authorize_params()
    )
    assert url.startswith(f"{KEYCLOAK_BASE}/protocol/openid-connect/auth?")


def test_build_authorize_url_contains_all_required_params():
    """All OIDC + PKCE query params from the Interstis guide must be present."""
    url = build_authorize_url(
        KEYCLOAK_BASE, CLIENT_ID, REDIRECT_URI, _authorize_params()
    )
    params = parse_qs(urlparse(url).query)
    assert params["response_type"] == ["code"]
    assert params["client_id"] == [CLIENT_ID]
    assert params["redirect_uri"] == [REDIRECT_URI]
    assert params["state"] == ["state123"]
    assert params["nonce"] == ["nonce123"]
    assert params["code_challenge"] == ["challenge123"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["scope"] == ["openid profile email offline_access"]


def test_build_authorize_url_accepts_a_custom_scope():
    """The default offline_access scope can be overridden by the caller."""
    url = build_authorize_url(
        KEYCLOAK_BASE, CLIENT_ID, REDIRECT_URI, _authorize_params(), scope="openid"
    )
    params = parse_qs(urlparse(url).query)
    assert params["scope"] == ["openid"]


# ---------------------------------------------------------------------------
# exchange_code_for_tokens()
# ---------------------------------------------------------------------------


def _migrator_client():
    return MigratorClient(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, redirect_uri=REDIRECT_URI
    )


def test_exchange_code_for_tokens_posts_to_the_token_endpoint():
    """The authorization_code grant must be posted with the PKCE code_verifier."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"access_token": "acc", "refresh_token": "off"}

    with patch(
        "core.sources.resana.migrator_auth.requests.post", return_value=mock_response
    ) as mock_post:
        exchange_code_for_tokens(
            KEYCLOAK_BASE, _migrator_client(), "the-code", "the-verifier"
        )

    mock_post.assert_called_once_with(
        f"{KEYCLOAK_BASE}/protocol/openid-connect/token",
        data={
            "grant_type": "authorization_code",
            "code": "the-code",
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code_verifier": "the-verifier",
        },
        timeout=30,
    )
    mock_response.raise_for_status.assert_called_once()


def test_exchange_code_for_tokens_returns_the_json_body():
    """The parsed token response (access_token, refresh_token, scope, ...) is returned as-is."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "access_token": "acc",
        "refresh_token": "off",
        "scope": "openid offline_access",
    }

    with patch(
        "core.sources.resana.migrator_auth.requests.post", return_value=mock_response
    ):
        result = exchange_code_for_tokens(
            KEYCLOAK_BASE, _migrator_client(), "the-code", "the-verifier"
        )

    assert result == {
        "access_token": "acc",
        "refresh_token": "off",
        "scope": "openid offline_access",
    }


def test_exchange_code_for_tokens_raises_on_http_error():
    """A rejected code (e.g. reused or expired) must propagate as an HTTPError."""
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.HTTPError("400 Bad Request")

    with patch(
        "core.sources.resana.migrator_auth.requests.post", return_value=mock_response
    ):
        with pytest.raises(requests.HTTPError):
            exchange_code_for_tokens(
                KEYCLOAK_BASE, _migrator_client(), "bad-code", "the-verifier"
            )


# ---------------------------------------------------------------------------
# refresh_offline_token()
# ---------------------------------------------------------------------------


def test_refresh_offline_token_posts_refresh_token_grant():
    """Renewal must use the standard refresh_token grant against Keycloak, not the old Auth Service."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "access_token": "new-acc",
        "refresh_token": "new-off",
    }

    with patch(
        "core.sources.resana.migrator_auth.requests.post", return_value=mock_response
    ) as mock_post:
        refresh_offline_token(KEYCLOAK_BASE, _migrator_client(), "old-off")

    mock_post.assert_called_once_with(
        f"{KEYCLOAK_BASE}/protocol/openid-connect/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": "old-off",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=30,
    )
    mock_response.raise_for_status.assert_called_once()


def test_refresh_offline_token_returns_the_json_body():
    """The new access/offline token pair is returned as-is for the caller to persist."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "access_token": "new-acc",
        "refresh_token": "new-off",
    }

    with patch(
        "core.sources.resana.migrator_auth.requests.post", return_value=mock_response
    ):
        result = refresh_offline_token(KEYCLOAK_BASE, _migrator_client(), "old-off")

    assert result == {"access_token": "new-acc", "refresh_token": "new-off"}


def test_refresh_offline_token_raises_on_http_error():
    """A revoked/expired offline token must propagate as an HTTPError."""
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.HTTPError("400 Bad Request")

    with patch(
        "core.sources.resana.migrator_auth.requests.post", return_value=mock_response
    ):
        with pytest.raises(requests.HTTPError):
            refresh_offline_token(KEYCLOAK_BASE, _migrator_client(), "revoked-off")


# ---------------------------------------------------------------------------
# bridge_session_url()
# ---------------------------------------------------------------------------


def test_bridge_session_url_appends_the_dinum_session_path():
    """The bridge path must be appended to the given Resana web base URL."""
    assert (
        bridge_session_url("https://resana.example.com")
        == "https://resana.example.com/public/auth/dinum-session"
    )


def test_bridge_session_url_does_not_double_slash():
    """A base URL without a trailing slash must not produce a double slash."""
    url = bridge_session_url("https://resana.example.com")
    assert "//public" not in url.replace("https://", "")


# ---------------------------------------------------------------------------
# create_bridge_session()
# ---------------------------------------------------------------------------


def _bridge_payload(**overrides):
    """Build a minimal valid bridge response payload, with optional field overrides."""
    payload = {
        "plateformeSessionId": "sess-id",
        "interstis_access": "bearer-jwt",
        "csrfToken": "csrf-value",
    }
    payload.update(overrides)
    return payload


def test_create_bridge_session_posts_with_bearer_access_token():
    """The bridge call must be a bare POST authenticated by the Keycloak access token."""
    mock_response = MagicMock()
    mock_response.json.return_value = _bridge_payload()

    with patch(
        "core.sources.resana.migrator_auth.requests.post", return_value=mock_response
    ) as mock_post:
        create_bridge_session(BRIDGE_URL, access_token="the-access-token")

    mock_post.assert_called_once_with(
        BRIDGE_URL,
        data=b"",
        headers={
            "Authorization": "Bearer the-access-token",
            "Accept": "application/json",
        },
        timeout=30,
    )
    mock_response.raise_for_status.assert_called_once()


def test_create_bridge_session_sends_existing_session_cookie_when_given():
    """Replacing an active or expired legacy session must not require its CSRF token."""
    mock_response = MagicMock()
    mock_response.json.return_value = _bridge_payload()

    with patch(
        "core.sources.resana.migrator_auth.requests.post", return_value=mock_response
    ) as mock_post:
        create_bridge_session(
            BRIDGE_URL, access_token="the-access-token", existing_session_id="old-sess"
        )

    assert mock_post.call_args.kwargs["headers"]["Cookie"] == "PHPSESSID=old-sess"


def test_create_bridge_session_omits_cookie_header_without_existing_session():
    """A first-time login must not send a Cookie header at all."""
    mock_response = MagicMock()
    mock_response.json.return_value = _bridge_payload()

    with patch(
        "core.sources.resana.migrator_auth.requests.post", return_value=mock_response
    ) as mock_post:
        create_bridge_session(BRIDGE_URL, access_token="the-access-token")

    assert "Cookie" not in mock_post.call_args.kwargs["headers"]


def test_create_bridge_session_returns_the_json_body():
    """The full bridge payload (session id, bearer, csrf token) is returned as-is."""
    mock_response = MagicMock()
    mock_response.json.return_value = _bridge_payload()

    with patch(
        "core.sources.resana.migrator_auth.requests.post", return_value=mock_response
    ):
        result = create_bridge_session(BRIDGE_URL, access_token="the-access-token")

    assert result == _bridge_payload()


def test_create_bridge_session_raises_on_http_error():
    """An unauthorized bearer must propagate as an HTTPError."""
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.HTTPError("401 Unauthorized")

    with patch(
        "core.sources.resana.migrator_auth.requests.post", return_value=mock_response
    ):
        with pytest.raises(requests.HTTPError):
            create_bridge_session(BRIDGE_URL, access_token="bad-token")


def test_create_bridge_session_raises_when_response_is_missing_fields():
    """A response missing one of the three required fields must fail loudly, not silently."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"plateformeSessionId": "sess-id"}

    with patch(
        "core.sources.resana.migrator_auth.requests.post", return_value=mock_response
    ):
        with pytest.raises(ValueError, match="interstis_access"):
            create_bridge_session(BRIDGE_URL, access_token="the-access-token")


@pytest.mark.parametrize("bad_value", [None, "", 42])
def test_create_bridge_session_raises_when_a_field_is_not_a_non_empty_string(bad_value):
    """Present-but-unusable values (null, empty, non-string) must be rejected like missing ones."""
    mock_response = MagicMock()
    mock_response.json.return_value = {**_bridge_payload(), "csrfToken": bad_value}

    with patch(
        "core.sources.resana.migrator_auth.requests.post", return_value=mock_response
    ):
        with pytest.raises(ValueError, match="csrfToken"):
            create_bridge_session(BRIDGE_URL, access_token="the-access-token")


def test_create_bridge_session_raises_when_response_is_not_an_object():
    """A JSON array or scalar body cannot carry the session and must fail loudly."""
    mock_response = MagicMock()
    mock_response.json.return_value = [
        "plateformeSessionId",
        "interstis_access",
        "csrfToken",
    ]

    with patch(
        "core.sources.resana.migrator_auth.requests.post", return_value=mock_response
    ):
        with pytest.raises(ValueError, match="not a JSON object"):
            create_bridge_session(BRIDGE_URL, access_token="the-access-token")
