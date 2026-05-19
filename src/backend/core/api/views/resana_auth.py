"""Resana authentication endpoints."""

import re

from django.conf import settings

import requests
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.permissions import IsAuthenticated
from core.sources.resana.token_manager import ResanaTokenManager


class _ConnectSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()


class ResanaAuthConnectView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = _ConnectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            access, refresh = _keycloak_login(
                serializer.validated_data["email"],
                serializer.validated_data["password"],
                keycloak_endpoint=settings.RESANA_KEYCLOAK_ENDPOINT,
                resana_auth_endpoint=settings.RESANA_AUTH_ENDPOINT,
            )
        except ValueError:
            return Response(
                {"error": "Authentication failed"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

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
        raise ValueError("Authentication failed — no interstis_access cookie received")

    return access, refresh or ""
