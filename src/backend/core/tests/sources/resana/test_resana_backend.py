"""Tests for ResanaSourceBackend."""

from unittest.mock import MagicMock, patch

import pytest

from core.backends.source import (
    AbstractSourceBackend,
    SourceFile,
    SourceFolder,
    SourceWorkspace,
)
from core.sources.resana.backend import ResanaSourceBackend

# ---------------------------------------------------------------------------
# Group 5.1 — Class contract
# ---------------------------------------------------------------------------


def test_implements_abstract_source_backend():
    assert issubclass(ResanaSourceBackend, AbstractSourceBackend)


def test_source_type_is_resana():
    assert ResanaSourceBackend.source_type == "resana"


# ---------------------------------------------------------------------------
# Group 5.2 — get_workspaces()
# ---------------------------------------------------------------------------


def test_get_workspaces_converts_raw_dicts_to_source_workspaces():
    raw_workspaces = [
        {
            "uuid": "ws-1",
            "name": "Espace Projet",
            "isPersonalWorkspace": False,
            "color": "#fff",
        },
        {
            "uuid": "ws-2",
            "name": "Mon espace",
            "isPersonalWorkspace": True,
            "color": "#abc",
        },
    ]

    with patch("core.sources.resana.backend.InterstisClient") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.get_workspaces.return_value = raw_workspaces

        backend = ResanaSourceBackend()
        user = MagicMock()
        result = backend.get_workspaces(user)

    mock_client.get_workspaces.assert_called_once()
    assert len(result) == 2
    assert all(isinstance(ws, SourceWorkspace) for ws in result)
    assert result[0].id == "ws-1"
    assert result[0].title == "Espace Projet"
    assert result[0].raw_data == raw_workspaces[0]
    assert result[1].id == "ws-2"
    assert result[1].title == "Mon espace"


# ---------------------------------------------------------------------------
# Group 6 — get_workspace_structure()
# ---------------------------------------------------------------------------


def test_get_workspace_structure_flat_folder():
    explore_root = [
        {
            "uuid": "ws-uuid",
            "name": "My Workspace",
            "folders": [
                {"uuid": "folder-1", "name": "Documents", "folders": [], "files": []},
            ],
            "files": [
                {"uuid": "file-1", "name": "readme", "extension": ".txt"},
            ],
        }
    ]

    workspace = MagicMock()
    workspace.source_id = "ws-uuid"

    with patch("core.sources.resana.backend.InterstisClient") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.explore.return_value = explore_root

        backend = ResanaSourceBackend()
        result = backend.get_workspace_structure(workspace)

    mock_client.explore.assert_called_once_with("ws-uuid")
    assert isinstance(result, SourceFolder)
    assert len(result.children) == 1
    assert result.children[0].name == "Documents"
    assert len(result.files) == 1
    f = result.files[0]
    assert isinstance(f, SourceFile)
    assert f.id == "file-1"
    assert f.name == "readme"
    assert f.extension == ".txt"
    assert f.download_url == "file-1"


def test_get_workspace_structure_nested_folders():
    explore_root = [
        {
            "uuid": "ws-uuid",
            "name": "Root",
            "folders": [
                {
                    "uuid": "parent-folder",
                    "name": "Parent",
                    "folders": [
                        {
                            "uuid": "child-folder",
                            "name": "Child",
                            "folders": [],
                            "files": [],
                        },
                    ],
                    "files": [],
                },
            ],
            "files": [],
        }
    ]

    workspace = MagicMock()
    workspace.source_id = "ws-uuid"

    with patch("core.sources.resana.backend.InterstisClient") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.explore.return_value = explore_root

        result = ResanaSourceBackend().get_workspace_structure(workspace)

    parent = result.children[0]
    assert parent.name == "Parent"
    child = parent.children[0]
    assert child.name == "Child"
    assert child.children == []
    assert child.files == []


def test_get_workspace_structure_empty_workspace():
    workspace = MagicMock()
    workspace.source_id = "ws-uuid"

    with patch("core.sources.resana.backend.InterstisClient") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.explore.return_value = []

        result = ResanaSourceBackend().get_workspace_structure(workspace)

    assert isinstance(result, SourceFolder)
    assert result.name == ""
    assert result.children == []
    assert result.files == []


def test_get_workspace_structure_normalises_extension_without_dot():
    explore_root = [
        {
            "uuid": "ws-uuid",
            "name": "Root",
            "folders": [],
            "files": [{"uuid": "f1", "name": "photo", "extension": "jpg"}],
        }
    ]

    workspace = MagicMock()
    workspace.source_id = "ws-uuid"

    with patch("core.sources.resana.backend.InterstisClient") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.explore.return_value = explore_root

        result = ResanaSourceBackend().get_workspace_structure(workspace)

    f = result.files[0]
    assert f.extension == ".jpg"
    assert f.name_with_extension == "photo.jpg"


def test_get_workspace_structure_file_without_extension():
    explore_root = [
        {
            "uuid": "ws-uuid",
            "name": "Root",
            "folders": [],
            "files": [{"uuid": "f1", "name": "README"}],
        }
    ]

    workspace = MagicMock()
    workspace.source_id = "ws-uuid"

    with patch("core.sources.resana.backend.InterstisClient") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.explore.return_value = explore_root

        result = ResanaSourceBackend().get_workspace_structure(workspace)

    f = result.files[0]
    assert f.extension == ""
    assert f.name_with_extension == "README"


# ---------------------------------------------------------------------------
# Group 7 — download_file()
# ---------------------------------------------------------------------------


def test_download_file_delegates_to_client():
    source_file = SourceFile(
        id="file-uuid-abc",
        name="document",
        extension=".pdf",
        download_url="file-uuid-abc",
    )

    with patch("core.sources.resana.backend.InterstisClient") as mock_cls:
        mock_client = mock_cls.return_value

        ResanaSourceBackend().download_file(source_file, "/tmp/document.pdf")

    mock_client.download_file.assert_called_once_with(
        "file-uuid-abc", "/tmp/document.pdf"
    )
