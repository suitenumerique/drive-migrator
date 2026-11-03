"""Tests for TribuDestinationBackend."""

import os
from unittest.mock import MagicMock, patch

import pytest

from core import factories
from core.backends.destination import AbstractDestinationBackend
from core.destinations.tribu.backend import TribuDestinationBackend, get_owner
from core.models import Workspace

MEMBERS = [
    {"name": "Lovelace", "firstName": "Ada", "email": "Ada@Example.com"},
    {"name": "Hopper", "firstName": "Grace", "email": "grace@example.com"},
]


def _workspace(members=None):
    workspace = MagicMock(spec=Workspace)
    workspace.id = "ws-1"
    workspace.source_id = "ws-source-1"
    workspace.title = "Workspace"
    workspace.members = members
    return workspace


@pytest.fixture(name="manager")
def fixture_manager():
    with patch("core.destinations.tribu.backend.ArchiveManager") as manager_cls:
        manager = manager_cls.return_value
        manager.get_archive_path.return_value = "/tmp/ws-1.zip"
        yield manager


@pytest.fixture(name="client")
def fixture_client():
    with patch("core.destinations.tribu.backend.UploadBrokerClient") as client_cls:
        yield client_cls.return_value


def test_export_uploads_the_archive(manager, client, tmp_path):
    """The archive is sent with the workspace ID, title and owner."""
    workspace = _workspace(MEMBERS)
    user = MagicMock(email="ada@example.com")

    TribuDestinationBackend().export(workspace, user, str(tmp_path))

    manager.zip_workspace_folder.assert_called_once_with(workspace)
    client.upload.assert_called_once_with(
        "/tmp/ws-1.zip",
        source_id="ws-source-1",
        title="Workspace",
        owner={
            "email": "ada@example.com",
            "first_name": "Ada",
            "last_name": "Lovelace",
        },
    )


def test_user_without_email_fails_before_the_zip(manager, client, tmp_path):
    """A user without email fails the export before the zip and the upload."""
    with pytest.raises(ValueError, match="no email"):
        TribuDestinationBackend().export(
            _workspace(MEMBERS), MagicMock(email=None), str(tmp_path)
        )

    manager.zip_workspace_folder.assert_not_called()
    client.upload.assert_not_called()


@pytest.mark.django_db
@pytest.mark.usefixtures("client")
def test_export_marks_the_destination_as_successful(manager, tmp_path):
    """When the archive is in the Tribu queue, the workspace migration succeeds."""
    workspace = factories.WorkspaceFactory(
        destination_statuses={"tribu": Workspace.Status.PENDING},
        status=Workspace.Status.PENDING,
    )

    TribuDestinationBackend().export(workspace, factories.UserFactory(), str(tmp_path))

    workspace.refresh_from_db()
    assert workspace.destination_statuses == {"tribu": Workspace.Status.SUCCESS}
    assert workspace.status == Workspace.Status.SUCCESS
    manager.delete_archive.assert_called_once_with(workspace)


@pytest.mark.django_db
def test_failed_upload_leaves_the_workspace_pending(manager, client, tmp_path):
    """If the upload fails, the archive is deleted and the status does not change."""
    workspace = factories.WorkspaceFactory(
        destination_statuses={"tribu": Workspace.Status.PENDING},
        status=Workspace.Status.PENDING,
    )
    client.upload.side_effect = RuntimeError("broker down")

    with pytest.raises(RuntimeError):
        TribuDestinationBackend().export(
            workspace, factories.UserFactory(), str(tmp_path)
        )

    workspace.refresh_from_db()
    assert workspace.destination_statuses == {"tribu": Workspace.Status.PENDING}
    assert workspace.status == Workspace.Status.PENDING
    manager.delete_archive.assert_called_once_with(workspace)


@pytest.mark.usefixtures("manager", "client")
def test_no_members_listing(tmp_path):
    """The archive holds the workspace files only, without a members CSV."""
    TribuDestinationBackend().export(
        _workspace(MEMBERS), MagicMock(email="a@example.com"), str(tmp_path)
    )

    assert not os.listdir(tmp_path)


def test_owner_names_come_from_the_members_list():
    """The owner is found in the members by email. The case does not matter."""
    owner = get_owner(_workspace(MEMBERS), MagicMock(email="ada@example.com"))

    assert owner == {
        "email": "ada@example.com",
        "first_name": "Ada",
        "last_name": "Lovelace",
    }


@pytest.mark.parametrize(
    "members",
    [
        None,
        [],
        [{"name": "Hopper", "firstName": "Grace", "email": "grace@example.com"}],
        [{"name": "", "firstName": "", "email": "ada@example.com"}],
    ],
)
def test_owner_names_fall_back_on_the_email(members):
    """If the member has no names, both names are the local part of the email."""
    owner = get_owner(_workspace(members), MagicMock(email="ada@example.com"))

    assert owner == {
        "email": "ada@example.com",
        "first_name": "ada",
        "last_name": "ada",
    }


def test_implements_abstract_destination():
    """The tribu destination implements the destination interface."""
    assert issubclass(TribuDestinationBackend, AbstractDestinationBackend)
    assert TribuDestinationBackend.name == "tribu"
    assert TribuDestinationBackend.label == "Tribu"
