"""URL patterns for the Resana source module."""

from django.urls import path

from core.api.views.resana_auth import (
    ResanaAuthCallbackView,
    ResanaAuthConnectView,
    ResanaAuthStatusView,
)

urlpatterns = [
    path("resana/auth/connect", ResanaAuthConnectView.as_view()),
    # Path fixed as "resana-auth/callback" (not "resana/auth/callback"): this
    # is the exact redirect_uri path communicated to and registered by
    # Interstis on the resana-migrator Keycloak client.
    path("resana-auth/callback", ResanaAuthCallbackView.as_view()),
    path("resana/auth/status", ResanaAuthStatusView.as_view()),
]
