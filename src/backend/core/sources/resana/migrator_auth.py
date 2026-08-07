"""PKCE auth flow against the dedicated resana-migrator Keycloak client, and the bridge session client."""

import base64
import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import requests

DEFAULT_SCOPE = "openid profile email offline_access"


@dataclass(frozen=True)
class MigratorClient:
    """Keycloak client identity for the resana-migrator client."""

    client_id: str
    client_secret: str
    redirect_uri: str


@dataclass(frozen=True)
class AuthorizeParams:
    """Per-attempt PKCE + OIDC parameters carried by the authorize redirect."""

    state: str
    nonce: str
    code_challenge: str


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def generate_pkce_pair() -> tuple[str, str]:
    """Return a fresh (code_verifier, code_challenge) pair for the PKCE S256 method."""
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def build_authorize_url(
    keycloak_base: str,
    client_id: str,
    redirect_uri: str,
    params: AuthorizeParams,
    scope: str = DEFAULT_SCOPE,
) -> str:
    """Build the Keycloak authorization endpoint URL to redirect the user's browser to."""
    query = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": params.state,
        "nonce": params.nonce,
        "code_challenge": params.code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{keycloak_base}/protocol/openid-connect/auth?{urlencode(query)}"


def exchange_code_for_tokens(
    keycloak_base: str, client: MigratorClient, code: str, code_verifier: str
) -> dict:
    """Exchange the authorization code for an access_token and offline refresh_token."""
    response = requests.post(
        f"{keycloak_base}/protocol/openid-connect/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": client.redirect_uri,
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "code_verifier": code_verifier,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def refresh_offline_token(
    keycloak_base: str, client: MigratorClient, offline_token: str
) -> dict:
    """Renew the access token from the stored offline token, directly against Keycloak."""
    response = requests.post(
        f"{keycloak_base}/protocol/openid-connect/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": offline_token,
            "client_id": client.client_id,
            "client_secret": client.client_secret,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def bridge_session_url(base_url: str) -> str:
    """Return the Resana bridge endpoint URL for the given web base URL."""
    return f"{base_url}/public/auth/dinum-session"


def create_bridge_session(
    bridge_url: str, access_token: str, existing_session_id: str | None = None
) -> dict:
    """Call the Resana bridge to turn a Keycloak access_token into a Resana session.

    Returns plateformeSessionId (= PHPSESSID), interstis_access (bearer for the
    GED API v1), and csrfToken. Passing existing_session_id lets the bridge
    replace an active or expired legacy session without needing its CSRF token.
    """
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    if existing_session_id:
        headers["Cookie"] = f"PHPSESSID={existing_session_id}"

    response = requests.post(bridge_url, data=b"", headers=headers, timeout=30)
    response.raise_for_status()
    payload = response.json()

    required_fields = ("plateformeSessionId", "interstis_access", "csrfToken")
    if not isinstance(payload, dict):
        raise ValueError("Bridge response is not a JSON object")
    invalid = [
        field
        for field in required_fields
        if not isinstance(payload.get(field), str) or not payload[field]
    ]
    if invalid:
        raise ValueError(
            f"Bridge response missing or invalid fields: {', '.join(invalid)}"
        )
    return payload
