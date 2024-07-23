"""URL configuration for the core app."""
from django.conf import settings
from django.urls import include, path

from mozilla_django_oidc.views import OIDCAuthenticationCallbackView
from rest_framework.routers import DefaultRouter

from core.api import viewsets
from core.api.views.dev import dev_view
from core.api.views.spoof import spoof_view
from core.api.views.synchronize import SynchronizeAPIView
from core.api.views.workspaces import WorkspacesViewset
from core.api.views.workspaces_process import WorkspacesProcessAPIView
from core.authentication.urls import urlpatterns as oidc_urls
from core.authentication.views import OIDCLogoutCallbackView

# - Main endpoints
router = DefaultRouter()
router.register("users", viewsets.UserViewSet, basename="users")
router.register("workspaces", WorkspacesViewset, basename="workspaces")

urlpatterns = [
    path(
        f"api/{settings.API_VERSION}/",
        include(
            [
                *router.urls,
                *oidc_urls,
                path("synchronize/", SynchronizeAPIView.as_view()),
                path("workspaces/process", WorkspacesProcessAPIView.as_view()),
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
