"""Unit tests for the Authentication Backends."""

from django.core.exceptions import SuspiciousOperation
from django.utils import timezone

import pytest
from cryptography.fernet import Fernet

from core import models
from core.authentication.backends import OIDCAuthenticationBackend
from core.encryption import decrypt_token
from core.factories import UserFactory

pytestmark = pytest.mark.django_db

TEST_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def set_encryption_key(settings):
    settings.OIDC_TOKENS_ENCRYPTION_KEY = TEST_KEY


def test_authentication_getter_existing_user_no_email(
    django_assert_num_queries, monkeypatch
):
    """
    If an existing user matches the user's info sub, the user should be returned.
    get_or_create_user() now also persists OIDC tokens (1 SELECT + 1 UPDATE).
    """

    klass = OIDCAuthenticationBackend()
    db_user = UserFactory()

    def get_userinfo_mocked(*args):
        return {"sub": db_user.sub}

    monkeypatch.setattr(OIDCAuthenticationBackend, "get_userinfo", get_userinfo_mocked)

    # 1 SELECT to find user + 1 SELECT for unique validation (full_clean) + 1 UPDATE for tokens
    with django_assert_num_queries(3):
        user = klass.get_or_create_user(
            access_token="test-token", id_token=None, payload=None
        )

    assert user == db_user


def test_authentication_getter_new_user_no_email(monkeypatch):
    """
    If no user matches the user's info sub, a user should be created.
    User's info doesn't contain an email, created user's email should be empty.
    """
    klass = OIDCAuthenticationBackend()

    def get_userinfo_mocked(*args):
        return {"sub": "123"}

    monkeypatch.setattr(OIDCAuthenticationBackend, "get_userinfo", get_userinfo_mocked)

    user = klass.get_or_create_user(
        access_token="test-token", id_token=None, payload=None
    )

    assert user.sub == "123"
    assert user.email is None
    assert user.password == "!"
    assert models.User.objects.count() == 1


def test_authentication_getter_new_user_with_email(monkeypatch):
    """
    If no user matches the user's info sub, a user should be created.
    User's email and name should be set on the identity.
    The "email" field on the User model should not be set as it is reserved for staff users.
    """
    klass = OIDCAuthenticationBackend()

    email = "impress@example.com"

    def get_userinfo_mocked(*args):
        return {"sub": "123", "email": email, "first_name": "John", "last_name": "Doe"}

    monkeypatch.setattr(OIDCAuthenticationBackend, "get_userinfo", get_userinfo_mocked)

    user = klass.get_or_create_user(
        access_token="test-token", id_token=None, payload=None
    )

    assert user.sub == "123"
    assert user.email == email
    assert user.password == "!"
    assert models.User.objects.count() == 1


def test_models_oidc_user_getter_invalid_token(django_assert_num_queries, monkeypatch):
    """The user's info doesn't contain a sub."""
    klass = OIDCAuthenticationBackend()

    def get_userinfo_mocked(*args):
        return {
            "test": "123",
        }

    monkeypatch.setattr(OIDCAuthenticationBackend, "get_userinfo", get_userinfo_mocked)

    with django_assert_num_queries(0), pytest.raises(
        SuspiciousOperation,
        match="User info contained no recognizable user identification",
    ):
        klass.get_or_create_user(access_token="test-token", id_token=None, payload=None)

    assert models.User.objects.exists() is False


# ---------------------------------------------------------------------------
# OIDC token persistence (for Drive user_token auth mode)
# ---------------------------------------------------------------------------


def test_get_or_create_user_persists_access_token_to_existing_user(monkeypatch):
    """get_or_create_user() saves the access_token on an existing user."""
    backend = OIDCAuthenticationBackend()
    db_user = UserFactory()
    backend._token_info = {"refresh_token": "", "expires_in": None}

    monkeypatch.setattr(
        OIDCAuthenticationBackend,
        "get_userinfo",
        lambda self, *a: {"sub": db_user.sub},
    )

    backend.get_or_create_user(
        access_token="new-access-tok", id_token=None, payload=None
    )

    db_user.refresh_from_db()
    assert decrypt_token(db_user.oidc_access_token) == "new-access-tok"


def test_get_or_create_user_persists_access_token_to_new_user(monkeypatch):
    """get_or_create_user() saves the access_token when creating a new user."""
    backend = OIDCAuthenticationBackend()
    backend._token_info = {"refresh_token": "", "expires_in": None}

    monkeypatch.setattr(
        OIDCAuthenticationBackend,
        "get_userinfo",
        lambda self, *a: {"sub": "brand-new-sub"},
    )

    user = backend.get_or_create_user(
        access_token="access-for-new-user", id_token=None, payload=None
    )

    assert decrypt_token(user.oidc_access_token) == "access-for-new-user"


def test_get_or_create_user_persists_refresh_token(monkeypatch):
    """get_or_create_user() saves the refresh_token from _token_info."""
    backend = OIDCAuthenticationBackend()
    db_user = UserFactory()
    backend._token_info = {"refresh_token": "my-refresh-tok", "expires_in": None}

    monkeypatch.setattr(
        OIDCAuthenticationBackend,
        "get_userinfo",
        lambda self, *a: {"sub": db_user.sub},
    )

    backend.get_or_create_user(access_token="tok", id_token=None, payload=None)

    db_user.refresh_from_db()
    assert decrypt_token(db_user.oidc_refresh_token) == "my-refresh-tok"


def test_get_or_create_user_persists_token_expiry(monkeypatch):
    """get_or_create_user() computes and saves oidc_token_expires_at from expires_in."""
    backend = OIDCAuthenticationBackend()
    db_user = UserFactory()
    backend._token_info = {"refresh_token": "", "expires_in": 60}

    monkeypatch.setattr(
        OIDCAuthenticationBackend,
        "get_userinfo",
        lambda self, *a: {"sub": db_user.sub},
    )

    before = timezone.now()
    backend.get_or_create_user(access_token="tok", id_token=None, payload=None)
    after = timezone.now()

    db_user.refresh_from_db()
    assert db_user.oidc_token_expires_at is not None
    assert (
        before < db_user.oidc_token_expires_at < after + timezone.timedelta(seconds=60)
    )


def test_get_or_create_user_leaves_expiry_null_when_no_expires_in(monkeypatch):
    """oidc_token_expires_at stays None when the token response has no expires_in."""
    backend = OIDCAuthenticationBackend()
    db_user = UserFactory(oidc_token_expires_at=None)
    backend._token_info = {"refresh_token": "", "expires_in": None}

    monkeypatch.setattr(
        OIDCAuthenticationBackend,
        "get_userinfo",
        lambda self, *a: {"sub": db_user.sub},
    )

    backend.get_or_create_user(access_token="tok", id_token=None, payload=None)

    db_user.refresh_from_db()
    assert db_user.oidc_token_expires_at is None


def test_get_token_stores_token_info_on_instance(monkeypatch):
    """get_token() stores the full token_info on self._token_info for later use."""
    backend = OIDCAuthenticationBackend()
    token_response = {
        "access_token": "acc",
        "refresh_token": "ref",
        "expires_in": 60,
        "id_token": "id",
    }
    monkeypatch.setattr(
        OIDCAuthenticationBackend.__bases__[0],
        "get_token",
        lambda self, payload: token_response,
    )

    backend.get_token({"some": "payload"})

    assert backend._token_info == token_response


# ---------------------------------------------------------------------------
# Token encryption at rest
# ---------------------------------------------------------------------------


def test_stored_access_token_is_encrypted_not_plaintext(monkeypatch):
    """The access_token written to DB must not be the raw plaintext value."""
    backend = OIDCAuthenticationBackend()
    db_user = UserFactory()
    backend._token_info = {"refresh_token": "", "expires_in": None}

    monkeypatch.setattr(
        OIDCAuthenticationBackend,
        "get_userinfo",
        lambda self, *a: {"sub": db_user.sub},
    )

    backend.get_or_create_user(
        access_token="plaintext-access", id_token=None, payload=None
    )

    db_user.refresh_from_db()
    assert db_user.oidc_access_token != "plaintext-access"


def test_stored_refresh_token_is_encrypted_not_plaintext(monkeypatch):
    """The refresh_token written to DB must not be the raw plaintext value."""
    backend = OIDCAuthenticationBackend()
    db_user = UserFactory()
    backend._token_info = {"refresh_token": "plaintext-refresh", "expires_in": None}

    monkeypatch.setattr(
        OIDCAuthenticationBackend,
        "get_userinfo",
        lambda self, *a: {"sub": db_user.sub},
    )

    backend.get_or_create_user(access_token="tok", id_token=None, payload=None)

    db_user.refresh_from_db()
    assert db_user.oidc_refresh_token != "plaintext-refresh"


def test_stored_tokens_decrypt_to_original_values(monkeypatch):
    """Tokens stored in DB decrypt back to the original plaintext values."""
    backend = OIDCAuthenticationBackend()
    db_user = UserFactory()
    backend._token_info = {"refresh_token": "my-refresh", "expires_in": None}

    monkeypatch.setattr(
        OIDCAuthenticationBackend,
        "get_userinfo",
        lambda self, *a: {"sub": db_user.sub},
    )

    backend.get_or_create_user(access_token="my-access", id_token=None, payload=None)

    db_user.refresh_from_db()
    assert decrypt_token(db_user.oidc_access_token) == "my-access"
    assert decrypt_token(db_user.oidc_refresh_token) == "my-refresh"


def test_empty_refresh_token_stored_as_empty_without_encryption(monkeypatch):
    """An empty refresh_token is stored as empty string, not as Fernet ciphertext."""
    backend = OIDCAuthenticationBackend()
    db_user = UserFactory()
    backend._token_info = {"refresh_token": "", "expires_in": None}

    monkeypatch.setattr(
        OIDCAuthenticationBackend,
        "get_userinfo",
        lambda self, *a: {"sub": db_user.sub},
    )

    backend.get_or_create_user(access_token="tok", id_token=None, payload=None)

    db_user.refresh_from_db()
    assert db_user.oidc_refresh_token == ""


# ---------------------------------------------------------------------------
# Restricted mode (issue #111): new users must be validated by an admin
# ---------------------------------------------------------------------------


def test_create_user_is_active_by_default_when_no_feature_flag_set():
    """With no FeatureFlag row, new users are active (current, non-restricted behavior)."""
    klass = OIDCAuthenticationBackend()

    user = klass.create_user({"sub": "123"})

    assert user.is_active is True


def test_create_user_is_active_when_auto_validate_flag_active():
    """Auto-validate flag active (explicit) -> new users are active."""
    models.FeatureFlag.objects.create(
        name=models.FeatureFlag.Name.AUTO_VALIDATE_NEW_USERS, is_active=True
    )
    klass = OIDCAuthenticationBackend()

    user = klass.create_user({"sub": "123"})

    assert user.is_active is True


def test_create_user_is_not_active_when_restricted_mode_enabled():
    """Auto-validate flag disabled (restricted mode) -> new users are inactive."""
    models.FeatureFlag.objects.create(
        name=models.FeatureFlag.Name.AUTO_VALIDATE_NEW_USERS, is_active=False
    )
    klass = OIDCAuthenticationBackend()

    user = klass.create_user({"sub": "123"})

    assert user.is_active is False


def test_get_or_create_user_returns_inactive_user_unchanged(monkeypatch):
    """get_or_create_user() still returns an inactive (not-yet-validated) user.

    Blocking the actual login is the OIDC callback view's responsibility
    (see OIDCAuthenticationCallbackView.failure_url), which checks `user.is_active`
    before calling login_success().
    """
    backend = OIDCAuthenticationBackend()
    db_user = UserFactory(is_active=False)

    monkeypatch.setattr(
        OIDCAuthenticationBackend,
        "get_userinfo",
        lambda self, *a: {"sub": db_user.sub},
    )

    user = backend.get_or_create_user(
        access_token="test-token", id_token=None, payload=None
    )

    assert user == db_user
    assert user.is_active is False
