"""Resana authentication endpoints (resana-migrator PKCE flow via the Interstis bridge)."""

import json
import secrets
from dataclasses import asdict, dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponseRedirect

import requests
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.permissions import IsAuthenticated
from core.encryption import decrypt_token, encrypt_token
from core.sources.resana.migrator_auth import (
    AuthorizeParams,
    bridge_session_url,
    build_authorize_url,
    create_bridge_session,
    exchange_code_for_tokens,
    generate_pkce_pair,
)
from core.sources.resana.token_manager import (
    ResanaTokenManager,
    migrator_client_from_settings,
)


@dataclass
class PendingAuth:
    """State + PKCE verifier kept server-side between connect() and callback()."""

    state: str
    nonce: str
    code_verifier: str


def _pending_auth_cache_key(user_id) -> str:
    return f"resana:migrator_auth:{user_id}"


def _store_pending_auth(user_id, pending: PendingAuth) -> None:
    """Cache the pending auth, Fernet-encrypted, keyed by user id."""
    payload = encrypt_token(json.dumps(asdict(pending)))
    cache.set(
        _pending_auth_cache_key(user_id),
        payload,
        timeout=settings.RESANA_MIGRATOR_STATE_TTL,
    )


def _load_pending_auth(user_id) -> PendingAuth | None:
    payload = cache.get(_pending_auth_cache_key(user_id))
    if payload is None:
        return None
    return PendingAuth(**json.loads(decrypt_token(payload)))


def _clear_pending_auth(user_id) -> None:
    cache.delete(_pending_auth_cache_key(user_id))


def _failure_redirect(error_code: str) -> HttpResponseRedirect:
    """Redirect to the failure URL, appending ?error= to its existing query string."""
    url = urlsplit(settings.RESANA_MIGRATOR_REDIRECT_URL_FAILURE)
    query = parse_qsl(url.query, keep_blank_values=True) + [("error", error_code)]
    return HttpResponseRedirect(urlunsplit(url._replace(query=urlencode(query))))


class _AuthExchangeFailed(Exception):
    """Raised by _complete_migrator_auth with the error_code to redirect with."""

    def __init__(self, error_code: str):
        super().__init__(error_code)
        self.error_code = error_code


def _complete_migrator_auth(user, code: str, code_verifier: str) -> None:
    """Exchange the code, call the bridge, and persist the resulting session.

    Raises _AuthExchangeFailed with an error_code describing which step failed.
    """
    try:
        token_response = exchange_code_for_tokens(
            settings.RESANA_KEYCLOAK_ENDPOINT,
            migrator_client_from_settings(),
            code,
            code_verifier,
        )
    except requests.RequestException as exc:
        raise _AuthExchangeFailed("token_exchange_failed") from exc

    access_token = token_response.get("access_token")
    offline_token = token_response.get("refresh_token")
    if not access_token or not offline_token:
        raise _AuthExchangeFailed("missing_tokens")

    bridge_url = bridge_session_url(settings.RESANA_WEB_ENDPOINT)
    try:
        session = create_bridge_session(bridge_url, access_token)
    except (requests.RequestException, ValueError) as exc:
        raise _AuthExchangeFailed("bridge_failed") from exc

    ResanaTokenManager(user).store_tokens(
        offline_token=offline_token,
        access_token=session["interstis_access"],
        session_id=session["plateformeSessionId"],
        csrf_token=session["csrfToken"],
    )


class ResanaAuthConnectView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        code_verifier, code_challenge = generate_pkce_pair()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        _store_pending_auth(
            request.user.id,
            PendingAuth(state=state, nonce=nonce, code_verifier=code_verifier),
        )

        authorize_url = build_authorize_url(
            settings.RESANA_KEYCLOAK_ENDPOINT,
            settings.RESANA_MIGRATOR_CLIENT_ID,
            settings.RESANA_MIGRATOR_REDIRECT_URI,
            AuthorizeParams(state=state, nonce=nonce, code_challenge=code_challenge),
        )
        return Response({"authorize_url": authorize_url})


class ResanaAuthCallbackView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.query_params.get("error"):
            return _failure_redirect(request.query_params["error"])

        code = request.query_params.get("code")
        state = request.query_params.get("state")
        if not code or not state:
            return _failure_redirect("missing_code_or_state")

        pending = _load_pending_auth(request.user.id)
        if pending is None or pending.state != state:
            return _failure_redirect("invalid_state")
        _clear_pending_auth(request.user.id)

        try:
            _complete_migrator_auth(request.user, code, pending.code_verifier)
        except _AuthExchangeFailed as exc:
            return _failure_redirect(exc.error_code)

        return HttpResponseRedirect(settings.RESANA_MIGRATOR_REDIRECT_URL_SUCCESS)


class ResanaAuthStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        manager = ResanaTokenManager(user)
        expires_at = user.resana_token_expires_at
        return Response(
            {
                "connected": manager.is_connected(),
                "expires_at": expires_at.isoformat() if expires_at else None,
            }
        )
