"""URL patterns for the Resana source module."""

from django.urls import path

from core.api.views.resana_auth import (
    ResanaAuthConnectView,
    ResanaAuthOtpView,
    ResanaAuthStatusView,
)

urlpatterns = [
    path("resana/auth/connect", ResanaAuthConnectView.as_view()),
    path("resana/auth/otp", ResanaAuthOtpView.as_view()),
    path("resana/auth/status", ResanaAuthStatusView.as_view()),
]
