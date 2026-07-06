"""Authentication Backends for the core app."""

from datetime import timedelta

from django.core.exceptions import SuspiciousOperation
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

import requests
from mozilla_django_oidc.auth import (
    OIDCAuthenticationBackend as MozillaOIDCAuthenticationBackend,
)

from core.encryption import encrypt_token
from core.models import FeatureFlag, User
from core.utils import is_feature


class OIDCAuthenticationBackend(MozillaOIDCAuthenticationBackend):
    """Custom OpenID Connect (OIDC) Authentication Backend.

    This class overrides the default OIDC Authentication Backend to accommodate differences
    in the User and Identity models, and handles signed and/or encrypted UserInfo response.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._token_info = {}

    def get_token(self, payload):
        """Intercept the token response to capture refresh_token/expires_in for persistence."""
        token_info = super().get_token(payload)
        self._token_info = token_info
        return token_info

    def get_userinfo(self, access_token, id_token, payload):
        """Return user details dictionary.

        Parameters:
        - access_token (str): The access token.
        - id_token (str): The id token (unused).
        - payload (dict): The token payload (unused).

        Note: The id_token and payload parameters are unused in this implementation,
        but were kept to preserve base method signature.

        Note: It handles signed and/or encrypted UserInfo Response. It is required by
        Agent Connect, which follows the OIDC standard. It forces us to override the
        base method, which deal with 'application/json' response.

        Returns:
        - dict: User details dictionary obtained from the OpenID Connect user endpoint.
        """

        user_response = requests.get(
            self.OIDC_OP_USER_ENDPOINT,
            headers={"Authorization": f"Bearer {access_token}"},
            verify=self.get_settings("OIDC_VERIFY_SSL", True),
            timeout=self.get_settings("OIDC_TIMEOUT", None),
            proxies=self.get_settings("OIDC_PROXY", None),
        )
        user_response.raise_for_status()
        # ProConnect returns a signed JWT; standard Keycloak returns plain JSON.
        if "application/json" in user_response.headers.get("Content-Type", ""):
            return user_response.json()
        return self.verify_token(user_response.text)

    def get_or_create_user(self, access_token, id_token, payload):
        """Return a User based on userinfo. Get or create a new user if no user matches the Sub.

        Parameters:
        - access_token (str): The access token.
        - id_token (str): The ID token.
        - payload (dict): The user payload.

        Returns:
        - User: An existing or newly created User instance.

        Raises:
        - Exception: Raised when user creation is not allowed and no existing user is found.
        """

        user_info = self.get_userinfo(access_token, id_token, payload)
        sub = user_info.get("sub")

        if sub is None:
            raise SuspiciousOperation(
                _("User info contained no recognizable user identification")
            )

        try:
            user = User.objects.get(sub=sub)
        except User.DoesNotExist:
            if self.get_settings("OIDC_CREATE_USER", True):
                user = self.create_user(user_info)
            else:
                user = None

        if user is not None:
            self._persist_oidc_tokens(user, access_token)

        return user

    def _persist_oidc_tokens(self, user, access_token: str) -> None:
        """Save access_token, refresh_token and expiry to the user model.

        Called after each successful login so Celery tasks can use the token
        for Drive migrations without needing a live HTTP session.
        """
        token_info = getattr(self, "_token_info", {})
        refresh_token = token_info.get("refresh_token") or ""
        expires_in = token_info.get("expires_in")

        user.oidc_access_token = encrypt_token(access_token)
        user.oidc_refresh_token = encrypt_token(refresh_token)
        user.oidc_token_expires_at = (
            timezone.now() + timedelta(seconds=expires_in) if expires_in else None
        )
        user.save(
            update_fields=[
                "oidc_access_token",
                "oidc_refresh_token",
                "oidc_token_expires_at",
                "updated_at",
            ]
        )

    def create_user(self, claims):
        """Return a newly created User instance."""

        sub = claims.get("sub")

        if sub is None:
            raise SuspiciousOperation(
                _("Claims contained no recognizable user identification")
            )

        user = User.objects.create(
            sub=sub,
            email=claims.get("email"),
            password="!",  # noqa: S106
            is_active=is_feature(FeatureFlag.Name.AUTO_VALIDATE_NEW_USERS),
        )

        return user
