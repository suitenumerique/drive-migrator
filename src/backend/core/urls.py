"""URL configuration for the core app."""
from django.conf import settings
from django.urls import include, path

from mozilla_django_oidc.views import OIDCAuthenticationCallbackView
from rest_framework.routers import DefaultRouter

from core.api import viewsets
from core.authentication.urls import urlpatterns as oidc_urls

# - Main endpoints
router = DefaultRouter()
router.register("users", viewsets.UserViewSet, basename="users")

urlpatterns = [
    path(
        f"api/{settings.API_VERSION}/",
        include(
            [
                *router.urls,
                *oidc_urls,
            ]
        ),
    ),
    path(
        "redirect",
        OIDCAuthenticationCallbackView.as_view(),
        name="oidc_authentication_callback",
    ),
    path(
        "logout/",
        OIDCLogoutCallbackView.as_view(),
        name="oidc_logout_callback",
    ),
    path(
        "spoof/",
        spoof_view,
    ),
    path(
        "dev/",
        dev_view,
    ),
]
