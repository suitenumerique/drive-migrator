"""Tests for GET /migration-config endpoint."""

from django.test import override_settings

import pytest
from rest_framework.test import APIClient

from core import factories
from core.backends.destination import DestinationRegistry

pytestmark = pytest.mark.django_db

ARCHIVE = "core.destinations.archive.backend.ArchiveDestinationBackend"
TRIBU = "core.destinations.tribu.backend.TribuDestinationBackend"


@pytest.fixture(autouse=True)
def clear_registry():
    DestinationRegistry.clear_cache()
    yield
    DestinationRegistry.clear_cache()


def test_migration_config_anonymous():
    """Anonymous users can read the migration config (used on public pages)."""
    client = APIClient()
    response = client.get("/api/v1.0/migration-config")
    assert response.status_code == 200


@override_settings(
    DESTINATION_BACKENDS=[ARCHIVE],
    MIGRATION_FILE_LIMIT_PER_WORKSPACE=0,
    DRIVE_FRONTEND_URL="https://drive.example.com",
    TRIBU_FRONTEND_URL="https://tribu.example.com",
    HELP_DOCUMENTATION_URL="https://help.example.com/docs",
    HELP_CONTACT_EMAIL="contact@example.com",
)
def test_migration_config_reports_unlimited():
    """When the limit setting is 0, the endpoint reports it as-is."""
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)

    response = client.get("/api/v1.0/migration-config")
    assert response.status_code == 200
    assert response.json() == {
        "file_limit_per_workspace": 0,
        "drive_frontend_url": "https://drive.example.com",
        "tribu_frontend_url": "https://tribu.example.com",
        "help_documentation_url": "https://help.example.com/docs",
        "help_contact_email": "contact@example.com",
        "destinations": ["archive"],
    }


@override_settings(
    DESTINATION_BACKENDS=[ARCHIVE],
    MIGRATION_FILE_LIMIT_PER_WORKSPACE=10,
    DRIVE_FRONTEND_URL="https://drive.example.com",
    TRIBU_FRONTEND_URL="https://tribu.example.com",
    HELP_DOCUMENTATION_URL="https://help.example.com/docs",
    HELP_CONTACT_EMAIL="contact@example.com",
)
def test_migration_config_reports_configured_limit():
    """When the limit setting is set, the endpoint reports its value."""
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)

    response = client.get("/api/v1.0/migration-config")
    assert response.status_code == 200
    assert response.json() == {
        "file_limit_per_workspace": 10,
        "drive_frontend_url": "https://drive.example.com",
        "tribu_frontend_url": "https://tribu.example.com",
        "help_documentation_url": "https://help.example.com/docs",
        "help_contact_email": "contact@example.com",
        "destinations": ["archive"],
    }


@override_settings(DRIVE_FRONTEND_URL="https://drive.example.com/custom")
def test_migration_config_reports_drive_frontend_url():
    """The endpoint reports the configured Drive frontend URL."""
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)

    response = client.get("/api/v1.0/migration-config")
    assert response.status_code == 200
    assert response.json()["drive_frontend_url"] == "https://drive.example.com/custom"


@override_settings(
    HELP_DOCUMENTATION_URL="https://help.example.com/docs",
    HELP_CONTACT_EMAIL="contact@example.com",
)
def test_migration_config_reports_help_config():
    """The endpoint reports the configured help documentation URL and contact email."""
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)

    response = client.get("/api/v1.0/migration-config")
    assert response.status_code == 200
    assert response.json()["help_documentation_url"] == "https://help.example.com/docs"
    assert response.json()["help_contact_email"] == "contact@example.com"


@override_settings(DESTINATION_BACKENDS=[ARCHIVE, TRIBU])
def test_migration_config_lists_the_destinations():
    """The endpoint lists the configured destinations, also for anonymous users."""
    response = APIClient().get("/api/v1.0/migration-config")

    assert response.status_code == 200
    assert response.json()["destinations"] == ["archive", "tribu"]
