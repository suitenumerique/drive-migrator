"""Authentication URLs for the core app."""

from django.urls import path

from mozilla_django_oidc.urls import urlpatterns as mozzila_oidc_urls

from .views import OIDCLogoutView

mozzila_oidc_urls_kept = []
for url in mozzila_oidc_urls:
    if url.name != "oidc_authentication_callback":
        mozzila_oidc_urls_kept.append(url)

urlpatterns = [
    # Override the default 'logout/' path from Mozilla Django OIDC with our custom view.
    path("logout/", OIDCLogoutView.as_view(), name="oidc_logout_custom"),
    *mozzila_oidc_urls_kept,
]
