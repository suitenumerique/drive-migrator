"""Tests for begin_export() grant-then-recover of Resana root folders."""

# pylint: disable=protected-access
import pytest

from core.factories import WorkspaceFactory
from core.sources.resana.backend import ResanaSourceBackend
from core.sources.resana.resana_lock_client import (
    ResanaChildDroitError,
    ResanaLockError,
    ResanaParentDroitError,
)
from core.tests.sources.resana.test_resana_backend_lock import (
    _stub_resana_clients,
)

pytestmark = pytest.mark.django_db


def test_begin_export_grants_unrestricted_root_without_unlocking(monkeypatch, settings):
    """An unrestricted root is saveDroit only; deverouille is not called."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = WorkspaceFactory(source_type="resana")

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch, top_level_folder_ids=["root"]
    )
    ResanaSourceBackend().begin_export(workspace)

    mock_lock.return_value.grant_folder_access.assert_called_once_with(
        "2137419", "root", "2040942"
    )
    mock_lock.return_value.release_folder_access.assert_not_called()
    workspace.refresh_from_db()
    assert workspace.source_lock_state["folders_granted_by_us"] == ["root"]
    assert not workspace.source_lock_state.get("permissions_removed")


def test_begin_export_skips_default_inbox_and_grants_other_roots(monkeypatch, settings):
    """The portal default folder (defaut == "1") cannot be limited; other roots still are."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = WorkspaceFactory(source_type="resana")

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(monkeypatch)
    mock_lock.return_value.list_folders.return_value = [
        {
            "id": "inbox",
            "name": "A classer",
            "dossier_mere": None,
            "defaut": "1",
        },
        {
            "id": "root",
            "name": "DOSSIER VERROUILLE USER 1",
            "dossier_mere": None,
        },
    ]
    ResanaSourceBackend().begin_export(workspace)

    mock_lock.return_value.grant_folder_access.assert_called_once_with(
        "2137419", "root", "2040942"
    )
    mock_lock.return_value.release_folder_access.assert_not_called()
    mock_lock.return_value.get_folder_access_owners.assert_called_once_with(
        "2137419", "root"
    )
    workspace.refresh_from_db()
    assert workspace.source_lock_state["folders_granted_by_us"] == ["root"]
    assert not workspace.source_lock_state.get("permissions_removed")


def test_begin_export_unlocks_root_limited_to_someone_else_then_grants(
    monkeypatch, settings
):
    """A root already limited to another user is unlocked, then granted."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = WorkspaceFactory(source_type="resana")
    order = []

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(monkeypatch)
    mock_lock.return_value.list_folders.return_value = [
        {
            "id": "root",
            "name": "DOSSIER VERROUILLE USER 1",
            "dossier_mere": None,
            "droit": {
                "type": "utilisateur",
                "tabId": ["999"],
            },
        }
    ]
    mock_lock.return_value.get_folder_access_owners.return_value = ["999"]
    mock_lock.return_value.release_folder_access.side_effect = (
        lambda _slug, folder_id: order.append(("release", folder_id))
    )
    mock_lock.return_value.grant_folder_access.side_effect = (
        lambda _slug, folder_id, _user_id: order.append(("grant", folder_id))
    )
    ResanaSourceBackend().begin_export(workspace)

    assert order == [("release", "root"), ("grant", "root")]
    workspace.refresh_from_db()
    assert workspace.source_lock_state["folders_granted_by_us"] == ["root"]
    assert workspace.source_lock_state["permissions_removed"] == [
        {"id": "root", "name": "DOSSIER VERROUILLE USER 1"}
    ]


def test_begin_export_does_not_unlock_on_an_unknown_grant_error(monkeypatch, settings):
    """A saveDroit failure that is not a known droit is raised without deverouille."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = WorkspaceFactory(source_type="resana")

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch, top_level_folder_ids=["root"]
    )
    mock_lock.return_value.grant_folder_access.side_effect = ResanaLockError("portal")

    with pytest.raises(ResanaLockError, match="portal"):
        ResanaSourceBackend().begin_export(workspace)

    mock_lock.return_value.release_folder_access.assert_not_called()
    workspace.refresh_from_db()
    assert "permissions_removed" not in (workspace.source_lock_state or {})
    assert workspace.source_lock_state["folders_granted_by_us"] == []


def test_begin_export_unlocks_child_then_grants_after_child_droit_error(
    monkeypatch, settings
):
    """Child droit on an unlocked root: grant fails, unlock descendants, grant."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = WorkspaceFactory(source_type="resana")
    order = []

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(monkeypatch)
    mock_lock.return_value.list_folders.return_value = [
        {"id": "root", "name": "Root", "dossier_mere": None},
        {
            "id": "verrouile",
            "name": "verrouile",
            "dossier_mere": "root",
            "droit": {"type": "utilisateur", "tabId": ["2040942"]},
        },
        {
            "id": "grand",
            "name": "Au fond",
            "dossier_mere": "verrouile",
            "droit": {"type": "utilisateur", "tabId": ["999"]},
        },
    ]

    def _grant(_slug, folder_id, _user_id):
        order.append(("grant", folder_id))
        if order.count(("grant", folder_id)) == 1:
            raise ResanaChildDroitError(
                "Un droit est déjà appliqué sur l'un des dossiers enfants !"
            )

    mock_lock.return_value.grant_folder_access.side_effect = _grant
    mock_lock.return_value.release_folder_access.side_effect = (
        lambda _slug, folder_id: order.append(("release", folder_id))
    )
    ResanaSourceBackend().begin_export(workspace)

    assert order == [
        ("grant", "root"),
        ("release", "verrouile"),
        ("release", "grand"),
        ("grant", "root"),
    ]
    workspace.refresh_from_db()
    assert workspace.source_lock_state["permissions_removed"] == [
        {"id": "verrouile", "name": "verrouile"},
        {"id": "grand", "name": "Au fond"},
    ]
    assert workspace.source_lock_state["folders_granted_by_us"] == ["root"]


def test_begin_export_unlocks_parent_then_child_when_parent_blocks_unlock(
    monkeypatch, settings
):
    """Locked root plus locked child: child unlock fails until the parent is dropped."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = WorkspaceFactory(source_type="resana")
    order = []
    unlocked = set()

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(monkeypatch)
    mock_lock.return_value.list_folders.return_value = [
        {
            "id": "root",
            "name": "DOSSIER VERROUILLE USER 1",
            "dossier_mere": None,
            "droit": {"type": "utilisateur", "tabId": ["2040942"]},
        },
        {
            "id": "test1",
            "name": "test1",
            "dossier_mere": "root",
            "droit": {"type": "utilisateur", "tabId": ["2040942"]},
        },
    ]

    def _grant(_slug, folder_id, _user_id):
        order.append(("grant", folder_id))
        if order.count(("grant", folder_id)) == 1:
            raise ResanaChildDroitError(
                "Un droit est déjà appliqué sur l'un des dossiers enfants !"
            )

    def _release(_slug, folder_id):
        order.append(("release", folder_id))
        if folder_id == "test1" and "root" not in unlocked:
            raise ResanaParentDroitError(
                "Un dossier parent vérouillé bloque cette action."
            )
        unlocked.add(folder_id)

    mock_lock.return_value.grant_folder_access.side_effect = _grant
    mock_lock.return_value.release_folder_access.side_effect = _release
    ResanaSourceBackend().begin_export(workspace)

    assert order == [
        ("grant", "root"),
        ("release", "test1"),
        ("release", "root"),
        ("release", "test1"),
        ("grant", "root"),
    ]
    workspace.refresh_from_db()
    assert workspace.source_lock_state["folders_granted_by_us"] == ["root"]
    assert workspace.source_lock_state["permissions_removed"] == [
        {"id": "root", "name": "DOSSIER VERROUILLE USER 1"},
        {"id": "test1", "name": "test1"},
    ]


def test_begin_export_raises_when_child_droit_remains_after_unlock(
    monkeypatch, settings
):
    """If saveDroit still hits a child-droit error after unlocking, fail the run."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = WorkspaceFactory(source_type="resana")

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(monkeypatch)
    mock_lock.return_value.list_folders.return_value = [
        {"id": "root", "name": "Root", "dossier_mere": None},
        {
            "id": "child",
            "name": "Secret",
            "dossier_mere": "root",
            "droit": {"type": "groupe", "tabId": ["10174"]},
        },
    ]
    mock_lock.return_value.grant_folder_access.side_effect = ResanaChildDroitError(
        "Un droit est déjà appliqué sur l'un des dossiers enfants !"
    )

    with pytest.raises(ResanaChildDroitError, match="dossiers enfants"):
        ResanaSourceBackend().begin_export(workspace)

    assert mock_lock.return_value.grant_folder_access.call_count == 2
    mock_lock.return_value.release_folder_access.assert_called_once_with(
        "2137419", "child"
    )
