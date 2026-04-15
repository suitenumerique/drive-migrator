"""Tests for GET /available-destinations/ endpoint."""

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from core import factories
from core.backends.destination import AbstractDestinationBackend, DestinationRegistry

pytestmark = pytest.mark.django_db


class _FakeArchive(AbstractDestinationBackend):
    name = "archive"
    label = "Archive ZIP"

    def export(self, workspace, user, local_folder_path):
        pass


class _FakeDrive(AbstractDestinationBackend):
    name = "drive"
    label = "La Suite Drive"

    def export(self, workspace, user, local_folder_path):
        pass


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
        {"name": "archive", "label": "Archive ZIP"},
        {"name": "drive", "label": "La Suite Drive"},
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
        {"name": "archive", "label": "Archive ZIP"},
    ]
