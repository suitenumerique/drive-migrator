"""Resana authentication endpoints."""

import json
import re
from dataclasses import asdict, dataclass

from django.conf import settings
from django.core.cache import cache

import requests
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.permissions import IsAuthenticated
from core.encryption import decrypt_token, encrypt_token
from core.sources.resana.token_manager import ResanaTokenManager

INTERSTIS_PORTAL_ATTRIBUTE_REGEX = re.compile(
    r'login_action="([^"]+)"[\s\S]*?'
    r'otp_input_only="([^"]*)"[\s\S]*?'
    r'selected_credentials="([^"]*)"'
)


@dataclass
class OtpChallenge:
    """Keycloak MFA challenge state, kept server-side between the password
    and OTP submission steps. Never carries the user's password."""

    login_action: str
    otp_field_name: str
    selected_credential_id: str
    cookies: dict[str, str]


class KeycloakOtpRequired(Exception):
    """Raised by _keycloak_login when Keycloak challenges with an OTP step."""

    def __init__(self, challenge: OtpChallenge):
        super().__init__("Keycloak requires an OTP code")
        self.challenge = challenge


def _otp_cache_key(user_id) -> str:
    return f"resana:mfa_challenge:{user_id}"


def _store_challenge(user_id, challenge: OtpChallenge) -> None:
    """Cache the challenge, Fernet-encrypted, so a Redis leak doesn't expose
    the in-progress Keycloak session cookies."""
    payload = encrypt_token(json.dumps(asdict(challenge)))
    cache.set(
        _otp_cache_key(user_id), payload, timeout=settings.RESANA_OTP_CHALLENGE_TTL
    )


def _load_challenge(user_id) -> OtpChallenge | None:
    payload = cache.get(_otp_cache_key(user_id))
    if payload is None:
        return None
    return OtpChallenge(**json.loads(decrypt_token(payload)))


class _ConnectSerializer(serializers.Serializer):
    password = serializers.CharField()


class ResanaAuthConnectView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = _ConnectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            access, refresh = _keycloak_login(
                request.user.email,
                serializer.validated_data["password"],
                keycloak_endpoint=settings.RESANA_KEYCLOAK_ENDPOINT,
                resana_auth_endpoint=settings.RESANA_AUTH_ENDPOINT,
            )
        except KeycloakOtpRequired as exc:
            _store_challenge(request.user.id, exc.challenge)
            return Response({"otp_required": True}, status=status.HTTP_200_OK)
        except ValueError:
            return Response(
                {"error": "Authentication failed"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        ResanaTokenManager(request.user).store_tokens(access, refresh)
        return Response(status=status.HTTP_200_OK)


class _OtpSerializer(serializers.Serializer):
    code = serializers.RegexField(r"^\d{6}$")


class ResanaAuthOtpView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = _OtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        challenge = _load_challenge(request.user.id)
        if challenge is None:
            return Response(
                {"error": "OTP challenge expired, please reconnect"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            access, refresh = _keycloak_submit_otp(
                serializer.validated_data["code"], challenge
            )
        except ValueError:
            return Response(
                {"error": "Invalid OTP code"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        cache.delete(_otp_cache_key(request.user.id))
        ResanaTokenManager(request.user).store_tokens(access, refresh)
        return Response(status=status.HTTP_200_OK)


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


def _keycloak_login(
    email: str,
    password: str,
    *,
    keycloak_endpoint: str,
    resana_auth_endpoint: str,
) -> tuple[str, str]:
    """Run the Keycloak authorization code flow server-side.

    Returns (access_token, refresh_token) as set by the Resana PHP backend.
    Raises ValueError if authentication fails.
    """
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"

    # Step 1 — fetch Keycloak login page
    r = session.get(
        f"{keycloak_endpoint}/protocol/openid-connect/auth",
        params={
            "scope": "openid",
            "response_type": "code",
            "approval_prompt": "auto",
            "redirect_uri": resana_auth_endpoint,
            "client_id": "interstis-plateforme",
        },
        timeout=15,
    )
    r.raise_for_status()

    match = re.search(r'action="([^"]+)"', r.text)
    if not match:
        raise ValueError("Keycloak login form not found")
    form_action = match.group(1).replace("&amp;", "&")

    # Step 2 — submit credentials
    r2 = session.post(
        form_action,
        data={"username": email, "password": password, "credentialId": ""},
        allow_redirects=True,
        timeout=15,
    )
    r2.raise_for_status()

    access = session.cookies.get("interstis_access")
    refresh = session.cookies.get("interstis_refresh")

    if not access:
        otp_match = INTERSTIS_PORTAL_ATTRIBUTE_REGEX.search(r2.text)
        if otp_match:
            login_action, otp_input_only, selected_credentials = otp_match.groups()
            raise KeycloakOtpRequired(
                OtpChallenge(
                    login_action=login_action.replace("&amp;", "&"),
                    otp_field_name="otp" if otp_input_only == "true" else "totp",
                    selected_credential_id=selected_credentials,
                    cookies=session.cookies.get_dict(),
                )
            )
        raise ValueError("Authentication failed — no interstis_access cookie received")

    return access, refresh or ""


def _keycloak_submit_otp(otp_code: str, challenge: OtpChallenge) -> tuple[str, str]:
    """Complete a Keycloak MFA challenge by posting the OTP code.

    Restores the Keycloak session from the challenge's saved cookies so the
    single-use session_code/execution from the original login attempt stays
    valid. Returns (access_token, refresh_token). Raises ValueError if the
    code is rejected.
    """
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"
    session.cookies.update(challenge.cookies)

    r = session.post(
        challenge.login_action,
        data={
            challenge.otp_field_name: otp_code,
            "selectedCredentialId": challenge.selected_credential_id,
            "userLabel": "Mon OTP",
        },
        allow_redirects=True,
        timeout=15,
    )
    r.raise_for_status()

    access = session.cookies.get("interstis_access")
    refresh = session.cookies.get("interstis_refresh")

    if not access:
        raise ValueError("Authentication failed — invalid OTP code")

    return access, refresh or ""
