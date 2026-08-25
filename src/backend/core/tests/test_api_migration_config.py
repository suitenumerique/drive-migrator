"""Tests for GET /migration-config endpoint."""

from django.test import override_settings

import pytest
from rest_framework.test import APIClient

from core import factories

pytestmark = pytest.mark.django_db


def test_migration_config_anonymous():
    """Anonymous users must not access the migration config."""
    client = APIClient()
    response = client.get("/api/v1.0/migration-config")
    assert response.status_code == 401


@override_settings(
    MIGRATION_FILE_LIMIT_PER_WORKSPACE=0,
    DRIVE_FRONTEND_URL="https://drive.example.com",
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
    }


@override_settings(
    MIGRATION_FILE_LIMIT_PER_WORKSPACE=10,
    DRIVE_FRONTEND_URL="https://drive.example.com",
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
