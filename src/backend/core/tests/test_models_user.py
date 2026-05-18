"""Tests for User model fields — Resana token storage."""

from django.utils import timezone

import pytest

from core.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_user_has_resana_access_token_field():
    user = UserFactory()
    assert hasattr(user, "resana_access_token")
    assert user.resana_access_token == ""


def test_user_has_resana_refresh_token_field():
    user = UserFactory()
    assert hasattr(user, "resana_refresh_token")
    assert user.resana_refresh_token == ""


def test_user_has_resana_token_expires_at_field():
    user = UserFactory()
    assert hasattr(user, "resana_token_expires_at")
    assert user.resana_token_expires_at is None


def test_resana_tokens_persist_to_db():
    user = UserFactory()
    now = timezone.now()
    user.resana_access_token = "encrypted-access"
    user.resana_refresh_token = "encrypted-refresh"
    user.resana_token_expires_at = now
    user.save()

    user.refresh_from_db()
    assert user.resana_access_token == "encrypted-access"
    assert user.resana_refresh_token == "encrypted-refresh"
    assert user.resana_token_expires_at is not None


def test_resana_tokens_factory_defaults():
    """Factory-created users start with empty Resana tokens."""
    user = UserFactory(resana_access_token="tok", resana_refresh_token="ref")
    assert user.resana_access_token == "tok"
    assert user.resana_refresh_token == "ref"
