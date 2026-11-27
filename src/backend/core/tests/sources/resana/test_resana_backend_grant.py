"""Tests for begin_export() grant-then-recover of Resana root folders."""

# pylint: disable=protected-access
from unittest.mock import MagicMock

import pytest

from core.backends.source import SourceFile
from core.factories import UserFactory, WorkspaceFactory
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


def test_begin_export_does_not_list_or_grant_files(monkeypatch, settings):
    """File-level droits are left in place; restricted files are downloaded later."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = WorkspaceFactory(source_type="resana")

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch, top_level_folder_ids=["folder"]
    )
    ResanaSourceBackend().begin_export(workspace)

    mock_lock.return_value.list_files.assert_not_called()
    mock_lock.return_value.grant_file_access.assert_not_called()


def _structure_explore(uuid, workspace_id="ws-uuid"):
    if uuid == workspace_id:
        return [
            {
                "folders": [
                    {"uuid": "folder-1", "name": "DOSSIER AVEC FICHIER USER 1"}
                ],
                "files": [],
            }
        ]
    return [
        {
            "folders": [],
            "files": [{"uuid": "ged-1", "name": "open", "extension": ".odt"}],
        }
    ]


def test_get_workspace_structure_adds_php_only_file(monkeypatch, settings):
    """A file GED explore omits is appended from the PHP listing."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = WorkspaceFactory(
        source_type="resana",
        migration_user=UserFactory(),
        source_lock_state={"slug": "2137419"},
    )

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch, stub_members=False, configure_lock=False
    )
    mock_ged = MagicMock()
    monkeypatch.setattr("core.sources.resana.backend.InterstisClient", mock_ged)
    mock_ged.return_value.explore.side_effect = lambda uuid: _structure_explore(
        uuid, workspace.source_id
    )
    mock_lock.return_value.list_folders.return_value = [
        {
            "id": "12677637",
            "name": "DOSSIER AVEC FICHIER USER 1",
            "dossier_mere": None,
        }
    ]
    mock_lock.return_value.list_files.return_value = [
        {
            "id": "48006411",
            "name": ("?-espace-administrateur,-contexte-pour-les-agents-de-code"),
            "extension": ".pdf",
        }
    ]
    result = ResanaSourceBackend().get_workspace_structure(workspace)

    folder = result.children[0]
    names = [item.name_with_extension for item in folder.files]
    assert "open.odt" in names
    extra = next(item for item in folder.files if item.id == "48006411")
    assert extra.name_with_extension == (
        "?-espace-administrateur,-contexte-pour-les-agents-de-code.pdf"
    )
    assert extra.raw_data == {"php": True, "slug": "2137419"}


def test_get_workspace_structure_skips_php_name_duplicates(monkeypatch, settings):
    """A PHP file already listed by GED is not appended again."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = WorkspaceFactory(
        source_type="resana",
        migration_user=UserFactory(),
        source_lock_state={"slug": "2137419"},
    )

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch, stub_members=False, configure_lock=False
    )
    mock_ged = MagicMock()
    monkeypatch.setattr("core.sources.resana.backend.InterstisClient", mock_ged)
    mock_ged.return_value.explore.side_effect = lambda uuid: _structure_explore(
        uuid, workspace.source_id
    )
    mock_lock.return_value.list_folders.return_value = [
        {
            "id": "12677637",
            "name": "DOSSIER AVEC FICHIER USER 1",
            "dossier_mere": None,
        }
    ]
    mock_lock.return_value.list_files.return_value = [
        {"id": "php-open", "name": "open.odt"}
    ]
    result = ResanaSourceBackend().get_workspace_structure(workspace)

    assert [item.id for item in result.children[0].files] == ["ged-1"]


def test_get_workspace_structure_keeps_restricted_file_on_name_clash(
    monkeypatch, settings
):
    """A restricted PHP file that shares a GED name is kept, and mailed."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = WorkspaceFactory(
        source_type="resana",
        migration_user=UserFactory(),
        source_lock_state={"slug": "2137419"},
    )

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch, stub_members=False, configure_lock=False
    )
    mock_ged = MagicMock()
    monkeypatch.setattr("core.sources.resana.backend.InterstisClient", mock_ged)
    mock_ged.return_value.explore.side_effect = lambda uuid: _structure_explore(
        uuid, workspace.source_id
    )
    mock_lock.return_value.list_folders.return_value = [
        {
            "id": "12677637",
            "name": "DOSSIER AVEC FICHIER USER 1",
            "dossier_mere": None,
        }
    ]
    mock_lock.return_value.list_files.return_value = [
        {
            "id": "48006411",
            "name": "open",
            "extension": ".odt",
            "droit": {"type": "utilisateur", "tabId": ["999"]},
        }
    ]
    result = ResanaSourceBackend().get_workspace_structure(workspace)

    names = [item.name_with_extension for item in result.children[0].files]
    assert "open.odt" in names
    assert "open (48006411).odt" in names
    workspace.refresh_from_db()
    assert workspace.source_lock_state["duplicate_files"] == [
        {"name": "open.odt", "path": "DOSSIER AVEC FICHIER USER 1"}
    ]


def test_get_workspace_structure_skips_ambiguous_ged_folder_names(
    monkeypatch, settings
):
    """PHP files under a path that matches two GED folders are not migrated."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = WorkspaceFactory(
        source_type="resana",
        migration_user=UserFactory(),
        source_lock_state={"slug": "2137419"},
    )

    def _explore(uuid):
        if uuid == workspace.source_id:
            return [
                {
                    "folders": [
                        {"uuid": "a", "name": "Doublon"},
                        {"uuid": "b", "name": "Doublon"},
                    ],
                    "files": [],
                }
            ]
        return [{"folders": [], "files": []}]

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch, stub_members=False, configure_lock=False
    )
    mock_ged = MagicMock()
    monkeypatch.setattr("core.sources.resana.backend.InterstisClient", mock_ged)
    mock_ged.return_value.explore.side_effect = _explore
    mock_lock.return_value.list_folders.return_value = [
        {"id": "php-1", "name": "Doublon", "dossier_mere": None}
    ]
    mock_lock.return_value.list_files.return_value = [
        {"id": "hidden", "name": "secret.odt"}
    ]
    result = ResanaSourceBackend().get_workspace_structure(workspace)

    assert all(not folder.files for folder in result.children)
    workspace.refresh_from_db()
    assert workspace.source_lock_state["duplicate_files"] == [
        {"name": "secret.odt", "path": "Doublon"}
    ]


def test_get_workspace_structure_skips_default_inbox_files(monkeypatch, settings):
    """The portal default folder is not migrated, including its PHP-listed files."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = WorkspaceFactory(
        source_type="resana",
        migration_user=UserFactory(),
        source_lock_state={"slug": "2137419"},
    )

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch, stub_members=False, configure_lock=False
    )
    mock_ged = MagicMock()
    monkeypatch.setattr("core.sources.resana.backend.InterstisClient", mock_ged)
    mock_ged.return_value.explore.side_effect = lambda uuid: (
        [
            {
                "folders": [{"uuid": "inbox-uuid", "name": "A classer"}],
                "files": [],
            }
        ]
        if uuid == workspace.source_id
        else [{"folders": [], "files": []}]
    )
    mock_lock.return_value.list_folders.return_value = [
        {
            "id": "inbox",
            "name": "A classer",
            "dossier_mere": None,
            "defaut": "1",
        }
    ]
    mock_lock.return_value.list_files.return_value = [
        {"id": "inbox-file", "name": "hidden.odt"}
    ]
    result = ResanaSourceBackend().get_workspace_structure(workspace)

    mock_lock.return_value.list_files.assert_not_called()
    assert result.children[0].files == []


def test_download_file_uses_php_portal_for_php_files(monkeypatch, settings):
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    backend = ResanaSourceBackend()
    backend._user = MagicMock()
    source_file = SourceFile(
        id="48006411",
        name="secret",
        extension=".pdf",
        download_url="48006411",
        raw_data={"php": True, "slug": "2137419"},
    )

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch, stub_members=False, configure_lock=False
    )
    mock_ged = MagicMock()
    monkeypatch.setattr("core.sources.resana.backend.InterstisClient", mock_ged)
    backend.download_file(source_file, "/tmp/secret.pdf")

    mock_lock.return_value.download_file.assert_called_once_with(
        "2137419", "48006411", "/tmp/secret.pdf"
    )
    mock_ged.return_value.download_file.assert_not_called()
