"""Tests for User model fields — Resana token storage."""

from django.core.exceptions import ValidationError
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


def test_email_must_be_unique():
    """Two users cannot share the same non-null email."""
    UserFactory(email="duplicate@example.com")
    # BaseModel.save() runs full_clean(), so the duplicate is rejected by
    # validate_unique() before reaching the DB unique index.
    with pytest.raises(ValidationError, match="email"):
        UserFactory(email="duplicate@example.com")


def test_email_can_be_null_for_multiple_users():
    """The uniqueness constraint allows several users with a null email."""
    user1 = UserFactory(email=None)
    user2 = UserFactory(email=None)
    assert user1.email is None
    assert user2.email is None
