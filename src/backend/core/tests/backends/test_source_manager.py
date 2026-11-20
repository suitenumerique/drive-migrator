"""Tests for SourceManager — settings-driven source backend loading and synchronization."""

from django.test import override_settings

import pytest

from core.backends.source import (
    AbstractSourceBackend,
    SourceFolder,
    SourceManager,
    SourceWorkspace,
)
from core.factories import UserFactory
from core.models import Workspace


class _FakeSource(AbstractSourceBackend):
    source_type = "fake"
    label = "Fake"

    def get_workspaces(self, user):
        return [
            SourceWorkspace(id="ws-1", title="Workspace One"),
            SourceWorkspace(id="ws-2", title="Workspace Two"),
        ]

    def get_workspace_structure(self, workspace):
        return SourceFolder(name="root")

    def download_file(self, file, destination_path):
        pass


FAKE_SOURCE_PATH = f"{_FakeSource.__module__}._FakeSource"


@pytest.mark.django_db
@override_settings(SOURCE_BACKEND=FAKE_SOURCE_PATH)
def test_get_backend_loads_from_settings():
    backend = SourceManager().get_backend()
    assert isinstance(backend, _FakeSource)


@pytest.mark.django_db
@override_settings(SOURCE_BACKEND=FAKE_SOURCE_PATH)
def test_synchronize_creates_workspaces():
    user = UserFactory()
    SourceManager().synchronize(user)

    assert Workspace.objects.count() == 2
    assert Workspace.objects.filter(
        source_id="ws-1", title="Workspace One", source_type="fake"
    ).exists()
    assert Workspace.objects.filter(
        source_id="ws-2", title="Workspace Two", source_type="fake"
    ).exists()
    assert user.workspaces.count() == 2


@pytest.mark.django_db
@override_settings(SOURCE_BACKEND=FAKE_SOURCE_PATH)
def test_synchronize_updates_existing_workspace():
    user = UserFactory()
    existing = Workspace.objects.create(
        source_id="ws-1", title="Old Title", source_type="fake"
    )

    SourceManager().synchronize(user)

    existing.refresh_from_db()
    assert existing.title == "Workspace One"
    # No duplicate created
    assert Workspace.objects.filter(source_id="ws-1").count() == 1


@pytest.mark.django_db
@override_settings(SOURCE_BACKEND=FAKE_SOURCE_PATH)
def test_synchronize_does_not_delete_vanished_workspaces():
    """Workspaces that disappear from source are preserved — this is a one-shot migration tool."""
    user = UserFactory()
    orphan = Workspace.objects.create(
        source_id="orphan", title="Orphan", source_type="fake"
    )
    user.workspaces.add(orphan)

    SourceManager().synchronize(user)

    # orphan is still in DB
    assert Workspace.objects.filter(source_id="orphan").exists()


class _OrgSource(AbstractSourceBackend):
    source_type = "fake"
    label = "Fake"

    def get_workspaces(self, user):
        return [
            SourceWorkspace(
                id="ws-org",
                title="With org",
                organization_id="12",
                organization_name="Mairie",
            ),
            SourceWorkspace(id="ws-plain", title="Plain"),
        ]

    def get_workspace_structure(self, workspace):
        return SourceFolder(name="root")

    def download_file(self, file, destination_path):
        pass


ORG_SOURCE_PATH = f"{_OrgSource.__module__}._OrgSource"


@pytest.mark.django_db
@override_settings(SOURCE_BACKEND=ORG_SOURCE_PATH)
def test_synchronize_creates_the_organization_when_the_source_names_it():
    """A named organization is created and linked. A workspace without one stays free."""
    user = UserFactory()
    SourceManager().synchronize(user)

    attached = Workspace.objects.get(source_id="ws-org")
    assert attached.organization.source_id == "12"
    assert attached.organization.name == "Mairie"
    assert Workspace.objects.get(source_id="ws-plain").organization is None

    attached.organization.name = "Ancien"
    attached.organization.save()
    SourceManager().synchronize(user)
    attached.organization.refresh_from_db()
    assert attached.organization.name == "Mairie"
