"""Tests for GET /available-destinations/ endpoint."""

from django.test import override_settings

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError
from rest_framework.test import APIClient

from core import factories
from core.backends.destination import (
    AbstractDestinationBackend,
    AccountPolicy,
    DestinationRegistry,
    DestinationTokenExpired,
)
from core.retrying_session import _max_attempts_cap

pytestmark = pytest.mark.django_db


class _FakeArchive(AbstractDestinationBackend):
    name = "archive"
    label = "Archive ZIP"

    def export(self, workspace, user, local_folder_path):
        pass


class _FakeDrive(AbstractDestinationBackend):
    name = "drive"
    label = "La Suite Drive"
    account_exists = True
    max_attempts_seen = None

    def export(self, workspace, user, local_folder_path):
        pass

    def get_account_policy(self):
        return AccountPolicy.USER_CREATED

    def user_account_exists(self, user):
        _FakeDrive.max_attempts_seen = _max_attempts_cap.get()
        return self.account_exists

    def get_account_creation_url(self):
        return "https://drive.example.com"


_ARCHIVE_PATH = f"{_FakeArchive.__module__}._FakeArchive"
_DRIVE_PATH = f"{_FakeDrive.__module__}._FakeDrive"


@pytest.fixture(autouse=True)
def clear_registry():
    DestinationRegistry.clear_cache()
    yield
    DestinationRegistry.clear_cache()


def test_available_destinations_anonymous():
    """Anonymous users must not access available destinations."""
    client = APIClient()
    response = client.get("/api/v1.0/available-destinations/")
    assert response.status_code == 401


@override_settings(DESTINATION_BACKENDS=[_ARCHIVE_PATH, _DRIVE_PATH])
def test_available_destinations_authenticated():
    """Authenticated users receive the list of configured destinations."""
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)

    response = client.get("/api/v1.0/available-destinations/")
    assert response.status_code == 200
    assert response.json() == [
        {
            "name": "archive",
            "label": "Archive ZIP",
            "account_policy": "none",
            "account_creation_url": None,
        },
        {
            "name": "drive",
            "label": "La Suite Drive",
            "account_policy": "user_created",
            "account_creation_url": "https://drive.example.com",
        },
    ]


@override_settings(DESTINATION_BACKENDS=[_ARCHIVE_PATH])
def test_available_destinations_single_backend():
    """Single-destination config returns a one-element list."""
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)

    response = client.get("/api/v1.0/available-destinations/")
    assert response.status_code == 200
    assert response.json() == [
        {
            "name": "archive",
            "label": "Archive ZIP",
            "account_policy": "none",
            "account_creation_url": None,
        },
    ]


def test_account_status_anonymous():
    """Anonymous users must not access a destination account status."""
    client = APIClient()
    response = client.get("/api/v1.0/available-destinations/drive/account-status/")
    assert response.status_code == 401


@override_settings(DESTINATION_BACKENDS=[_ARCHIVE_PATH, _DRIVE_PATH])
@pytest.mark.parametrize("exists", [True, False])
def test_account_status_tells_whether_the_account_exists(exists, monkeypatch):
    """The status says whether the user already has an account on the destination."""
    monkeypatch.setattr(_FakeDrive, "account_exists", exists)
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)

    response = client.get("/api/v1.0/available-destinations/drive/account-status/")
    assert response.status_code == 200
    assert response.json() == {"exists": exists}


@override_settings(DESTINATION_BACKENDS=[_ARCHIVE_PATH])
def test_account_status_of_a_destination_without_account():
    """A destination involving no user account never lacks one."""
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)

    response = client.get("/api/v1.0/available-destinations/archive/account-status/")
    assert response.status_code == 200
    assert response.json() == {"exists": True}


@override_settings(DESTINATION_BACKENDS=[_ARCHIVE_PATH])
def test_account_status_of_an_unknown_destination():
    """An unconfigured destination is not found."""
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)

    response = client.get("/api/v1.0/available-destinations/drive/account-status/")
    assert response.status_code == 404


def _failing_account_check(error):
    """A user_account_exists() that cannot conclude, raising error."""

    def user_account_exists(self, user):
        raise error

    return user_account_exists


def _account_status(client_user=None):
    """GET the drive account status as a logged-in user."""
    client = APIClient()
    client.force_login(client_user or factories.UserFactory())
    return client.get("/api/v1.0/available-destinations/drive/account-status/")


@override_settings(DESTINATION_BACKENDS=[_DRIVE_PATH])
def test_account_status_tries_the_destination_once():
    """Inside a web request, the destination is not retried past the server timeout."""
    response = _account_status()

    assert response.status_code == 200
    assert _FakeDrive.max_attempts_seen == 1


@override_settings(DESTINATION_BACKENDS=[_DRIVE_PATH])
def test_account_status_asks_to_log_in_again_when_the_token_expired(monkeypatch):
    """An unusable token is not a missing account: the user must log in again."""
    monkeypatch.setattr(
        _FakeDrive,
        "user_account_exists",
        _failing_account_check(DestinationTokenExpired()),
    )

    response = _account_status()

    assert response.status_code == 403
    assert response.json()["error_name"] == "DriveTokenRequired"


@override_settings(DESTINATION_BACKENDS=[_DRIVE_PATH])
def test_account_status_reports_an_unreachable_destination(monkeypatch, caplog):
    """A destination that cannot be reached is not a missing account either, and
    is logged: a misconfiguration would look the same."""
    monkeypatch.setattr(
        _FakeDrive,
        "user_account_exists",
        _failing_account_check(RequestsConnectionError()),
    )

    response = _account_status()

    assert response.status_code == 403
    assert response.json()["error_name"] == "DestinationUnavailable"
    assert "Could not check the user account on drive" in caplog.text
