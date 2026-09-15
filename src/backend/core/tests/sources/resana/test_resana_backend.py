"""Tests for ResanaSourceBackend."""

# pylint: disable=protected-access
from unittest.mock import MagicMock, patch

import pytest

from core.backends.source import (
    AbstractSourceBackend,
    SourceFile,
    SourceFolder,
    SourceWorkspace,
)
from core.sources.resana.backend import ResanaSourceBackend
from core.sources.resana.token_manager import ResanaTokenExpired

pytestmark = pytest.mark.django_db


def _make_workspace(user=None):
    ws = MagicMock()
    ws.source_id = "ws-uuid"
    ws.migration_user = user or MagicMock()
    return ws


# ---------------------------------------------------------------------------
# Class contract
# ---------------------------------------------------------------------------


def test_implements_abstract_source_backend():
    """ResanaSourceBackend satisfies the AbstractSourceBackend interface."""
    assert issubclass(ResanaSourceBackend, AbstractSourceBackend)


def test_source_type_is_resana():
    """source_type class attribute is set to 'resana'."""
    assert ResanaSourceBackend.source_type == "resana"


# ---------------------------------------------------------------------------
# _get_client() — token resolution
# ---------------------------------------------------------------------------


def test_get_client_uses_token_manager(settings):
    """_get_client() resolves a token via ResanaTokenManager and passes it to InterstisClient."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    user = MagicMock()
    backend = ResanaSourceBackend()
    backend._user = user

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.InterstisClient") as mock_client:
            backend._get_client()

    mock_tm.assert_called_once_with(user)
    mock_client.assert_called_once_with("tok")


def test_get_client_raises_when_no_user_set(settings):
    """_get_client() raises RuntimeError if no user has been set on the backend."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    backend = ResanaSourceBackend()

    with pytest.raises(RuntimeError, match="No user context"):
        backend._get_client()


def test_get_client_propagates_token_expired(settings):
    """_get_client() lets ResanaTokenExpired bubble up when the token is invalid."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    user = MagicMock()
    backend = ResanaSourceBackend()
    backend._user = user

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.side_effect = ResanaTokenExpired("expired")

        with pytest.raises(ResanaTokenExpired):
            backend._get_client()


# ---------------------------------------------------------------------------
# get_workspaces()
# ---------------------------------------------------------------------------


def test_get_workspaces_converts_raw_dicts_to_source_workspaces(settings):
    """get_workspaces() converts each raw workspace dict into a SourceWorkspace."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    raw_workspaces = [
        {"uuid": "ws-1", "name": "Espace Projet", "isPersonalWorkspace": False},
        {"uuid": "ws-2", "name": "Mon espace", "isPersonalWorkspace": True},
    ]
    user = MagicMock()

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.InterstisClient") as mock_client:
            mock_client.return_value.get_workspaces.return_value = raw_workspaces
            result = ResanaSourceBackend().get_workspaces(user)

    assert len(result) == 2
    assert all(isinstance(ws, SourceWorkspace) for ws in result)
    assert result[0].id == "ws-1"
    assert result[0].title == "Espace Projet"
    assert result[1].id == "ws-2"


def test_get_workspaces_unescapes_html_entities_in_title(settings):
    """Resana returns names HTML-escaped (e.g. &#039; for apostrophe); decode them."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    raw_workspaces = [
        {
            "uuid": "ws-1",
            "name": "Rapports d&#039;activit&eacute; &amp; suivi",
            "isPersonalWorkspace": False,
        }
    ]
    user = MagicMock()

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.InterstisClient") as mock_client:
            mock_client.return_value.get_workspaces.return_value = raw_workspaces
            result = ResanaSourceBackend().get_workspaces(user)

    assert result[0].title == "Rapports d'activité & suivi"


def test_get_workspaces_stores_user_on_backend(settings):
    """get_workspaces() stores the user on the backend so _get_client() can use it later."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    user = MagicMock()
    backend = ResanaSourceBackend()

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.InterstisClient") as mock_client:
            mock_client.return_value.get_workspaces.return_value = []
            backend.get_workspaces(user)

    assert backend._user is user


# ---------------------------------------------------------------------------
# get_workspace_structure()
# ---------------------------------------------------------------------------


def test_get_workspace_structure_stores_migration_user(settings):
    """get_workspace_structure() sets _user from workspace.migration_user before fetching."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    user = MagicMock()
    workspace = _make_workspace(user)
    backend = ResanaSourceBackend()

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.InterstisClient") as mock_client:
            mock_client.return_value.explore.return_value = []
            backend.get_workspace_structure(workspace)

    assert backend._user is user


def test_get_workspace_structure_flat_folder(settings):
    """get_workspace_structure() converts a single-level API response into SourceFolder with children and files."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    workspace = _make_workspace()

    def explore_side_effect(uuid):
        if uuid == workspace.source_id:
            return [
                {
                    "folders": [{"uuid": "folder-1", "name": "Documents"}],
                    "files": [
                        {"uuid": "file-1", "name": "readme", "extension": ".txt"}
                    ],
                }
            ]
        return []

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.InterstisClient") as mock_client:
            mock_client.return_value.explore.side_effect = explore_side_effect
            result = ResanaSourceBackend().get_workspace_structure(workspace)

    assert isinstance(result, SourceFolder)
    assert len(result.children) == 1
    assert result.children[0].name == "Documents"
    assert len(result.files) == 1
    f = result.files[0]
    assert f.id == "file-1"
    assert f.name == "readme"
    assert f.extension == ".txt"
    assert f.download_url == "file-1"


def test_get_workspace_structure_unescapes_html_entities_in_names(settings):
    """Folder and file names from Resana are HTML-unescaped before use."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    workspace = _make_workspace()

    def explore_side_effect(uuid):
        if uuid == workspace.source_id:
            return [
                {
                    "folders": [
                        {"uuid": "folder-1", "name": "Suivi budg&eacute;taire &amp; RH"}
                    ],
                    "files": [
                        {
                            "uuid": "file-1",
                            "name": "Compte-rendu &lt;final&gt; d&#039;&eacute;quipe",
                            "extension": ".txt",
                        }
                    ],
                }
            ]
        return []

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.InterstisClient") as mock_client:
            mock_client.return_value.explore.side_effect = explore_side_effect
            result = ResanaSourceBackend().get_workspace_structure(workspace)

    assert result.children[0].name == "Suivi budgétaire & RH"
    assert result.files[0].name == "Compte-rendu <final> d'équipe"


def test_get_workspace_structure_empty_workspace(settings):
    """get_workspace_structure() returns an empty SourceFolder when the API returns no members."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    workspace = _make_workspace()

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.InterstisClient") as mock_client:
            mock_client.return_value.explore.return_value = []
            result = ResanaSourceBackend().get_workspace_structure(workspace)

    assert isinstance(result, SourceFolder)
    assert result.name == ""


def test_get_workspace_structure_normalises_extension_without_dot(settings):
    """Extensions returned without a leading dot are normalised to include one."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    workspace = _make_workspace()

    def explore_side_effect(uuid):
        if uuid == workspace.source_id:
            return [
                {
                    "folders": [],
                    "files": [{"uuid": "f1", "name": "photo", "extension": "jpg"}],
                }
            ]
        return []

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.InterstisClient") as mock_client:
            mock_client.return_value.explore.side_effect = explore_side_effect
            result = ResanaSourceBackend().get_workspace_structure(workspace)

    assert result.files[0].extension == ".jpg"


def test_get_workspace_structure_recurses_into_subfolders(settings):
    """explore() must be called for each child folder UUID — not just the root."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    workspace = _make_workspace()

    def explore_side_effect(uuid):
        if uuid == workspace.source_id:
            return [
                {"folders": [{"uuid": "folder-1", "name": "Documents"}], "files": []}
            ]
        if uuid == "folder-1":
            return [
                {
                    "folders": [],
                    "files": [{"uuid": "file-2", "name": "report", "extension": "pdf"}],
                }
            ]
        return []

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.InterstisClient") as mock_client:
            mock_client.return_value.explore.side_effect = explore_side_effect
            result = ResanaSourceBackend().get_workspace_structure(workspace)

    assert len(result.children) == 1
    subfolder = result.children[0]
    assert subfolder.name == "Documents"
    assert len(subfolder.files) == 1
    assert subfolder.files[0].name == "report"
    assert subfolder.files[0].extension == ".pdf"
    mock_client.return_value.explore.assert_any_call("folder-1")


def test_get_workspace_structure_deep_nesting(settings):
    """Two levels of subfolders are fully explored recursively."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    workspace = _make_workspace()

    def explore_side_effect(uuid):
        if uuid == workspace.source_id:
            return [{"folders": [{"uuid": "lvl1", "name": "Level1"}], "files": []}]
        if uuid == "lvl1":
            return [{"folders": [{"uuid": "lvl2", "name": "Level2"}], "files": []}]
        if uuid == "lvl2":
            return [
                {
                    "folders": [],
                    "files": [{"uuid": "f1", "name": "deep", "extension": "txt"}],
                }
            ]
        return []

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.InterstisClient") as mock_client:
            mock_client.return_value.explore.side_effect = explore_side_effect
            result = ResanaSourceBackend().get_workspace_structure(workspace)

    lvl1 = result.children[0]
    assert lvl1.name == "Level1"
    lvl2 = lvl1.children[0]
    assert lvl2.name == "Level2"
    assert len(lvl2.files) == 1
    assert lvl2.files[0].name == "deep"


def test_get_workspace_structure_empty_subfolder_does_not_crash(settings):
    """A subfolder whose explore returns [] yields an empty SourceFolder."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    workspace = _make_workspace()

    def explore_side_effect(uuid):
        if uuid == workspace.source_id:
            return [{"folders": [{"uuid": "empty-f", "name": "Empty"}], "files": []}]
        return []

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.InterstisClient") as mock_client:
            mock_client.return_value.explore.side_effect = explore_side_effect
            result = ResanaSourceBackend().get_workspace_structure(workspace)

    assert len(result.children) == 1
    assert result.children[0].name == "Empty"
    assert result.children[0].files == []
    assert result.children[0].children == []


# ---------------------------------------------------------------------------
# download_file()
# ---------------------------------------------------------------------------


def test_download_file_uses_stored_user(settings):
    """download_file() authenticates with the stored user and delegates to InterstisClient."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    user = MagicMock()
    backend = ResanaSourceBackend()
    backend._user = user
    source_file = SourceFile(
        id="file-uuid", name="doc", extension=".pdf", download_url="file-uuid"
    )

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.InterstisClient") as mock_client:
            backend.download_file(source_file, "/tmp/doc.pdf")

    mock_tm.assert_called_once_with(user)
    mock_client.return_value.download_file.assert_called_once_with(
        "file-uuid", "/tmp/doc.pdf"
    )


def test_download_file_raises_when_no_user_set(settings):
    """download_file() raises RuntimeError when no user context has been set."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    source_file = SourceFile(
        id="file-uuid", name="doc", extension=".pdf", download_url="file-uuid"
    )
    backend = ResanaSourceBackend()

    with pytest.raises(RuntimeError, match="No user context"):
        backend.download_file(source_file, "/tmp/doc.pdf")


# ---------------------------------------------------------------------------
# prepare_export()
# ---------------------------------------------------------------------------


def _patch_members_client(mock_cls, slug="2137419", members=None):
    mock_cls.return_value.find_slug_by_workspace_name.return_value = slug
    mock_cls.return_value.list_workspace_members.return_value = members or []


def test_prepare_export_populates_members_when_workspace_found(settings):
    """prepare_export() sets workspace.members from the resolved slug's member list."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"
    members = [
        {"name": "Dupont", "firstName": "Jean", "email": "jean.dupont@example.test"}
    ]

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_client:
            _patch_members_client(mock_client, members=members)
            ResanaSourceBackend().prepare_export(workspace, "/tmp/workdir")

    assert workspace.members == members
    workspace.save.assert_called_once()


def test_prepare_export_resolves_slug_by_workspace_title(settings):
    """prepare_export() looks up the PHP slug using workspace.title."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_client:
            _patch_members_client(mock_client)
            ResanaSourceBackend().prepare_export(workspace, "/tmp/workdir")

    mock_client.return_value.find_slug_by_workspace_name.assert_called_once_with(
        "TEST Worskspace"
    )


def test_prepare_export_does_nothing_when_slug_not_found(settings):
    """prepare_export() leaves workspace.members untouched when no matching slug is found."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "Unknown workspace"

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_client:
            _patch_members_client(mock_client, slug=None)
            ResanaSourceBackend().prepare_export(workspace, "/tmp/workdir")

    mock_client.return_value.list_workspace_members.assert_not_called()
    workspace.save.assert_not_called()


def test_prepare_export_uses_migration_user_token(settings):
    """prepare_export() authenticates via the workspace's migration_user."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    user = MagicMock()
    workspace = _make_workspace(user)
    workspace.title = "TEST Worskspace"

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        mock_tm.return_value.get_session_id.return_value = "sess-id"
        mock_tm.return_value.get_csrf_token.return_value = "csrf-value"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_client:
            _patch_members_client(mock_client)
            ResanaSourceBackend().prepare_export(workspace, "/tmp/workdir")

    mock_tm.assert_called_once_with(user)
    mock_client.assert_called_once_with(
        access_token="tok",
        session_id="sess-id",
        csrf_token="csrf-value",
        base_url="https://resana-web.example.test",
    )
