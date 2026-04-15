"""Tests for OsmoseSourceBackend."""

from unittest.mock import MagicMock, patch

from core.backends.source import (
    AbstractSourceBackend,
    SourceFile,
    SourceFolder,
    SourceWorkspace,
)
from core.sources.osmose.backend import OsmoseSourceBackend


def test_implements_abstract_source_backend():
    """OsmoseSourceBackend must be a concrete implementation of AbstractSourceBackend."""
    assert issubclass(OsmoseSourceBackend, AbstractSourceBackend)


def test_source_type_is_osmose():
    """source_type must be 'osmose'."""
    assert OsmoseSourceBackend.source_type == "osmose"


def test_get_workspaces_calls_real_backend():
    """get_workspaces() delegates to OsmoseRealBackend and converts to SourceWorkspace."""
    mock_ws = MagicMock()
    mock_ws.id = "ws-1"
    mock_ws.title = "My Workspace"
    mock_ws.raw_data = {"id": "ws-1"}

    with patch("core.sources.osmose.backend.OsmoseRealBackend") as mock_real_cls:
        mock_real = mock_real_cls.return_value
        mock_real.get_workspaces.return_value = [mock_ws]

        user = MagicMock()
        backend = OsmoseSourceBackend()
        result = backend.get_workspaces(user)

    mock_real.get_workspaces.assert_called_once_with(user)
    assert len(result) == 1
    assert isinstance(result[0], SourceWorkspace)
    assert result[0].id == "ws-1"
    assert result[0].title == "My Workspace"


def test_get_workspace_structure_returns_source_folder(settings):
    """get_workspace_structure() converts OsmoseFolder tree to SourceFolder tree."""
    settings.OSMOSE_BASE_ENDPOINT = "http://osmose.example.com"

    mock_file = MagicMock()
    mock_file.name = "rapport"
    mock_file.extension = ".pdf"
    mock_file.raw_data = {"id": "f1", "downloadUrl": "files/rapport.pdf"}

    mock_child = MagicMock()
    mock_child.name = "Dossier"
    mock_child.children = []
    mock_child.files = [mock_file]
    mock_child.raw_data = {"id": "c1"}

    mock_root = MagicMock()
    mock_root.name = ""
    mock_root.children = [mock_child]
    mock_root.files = []

    with patch("core.sources.osmose.backend.OsmoseRealBackend") as mock_real_cls:
        mock_real = mock_real_cls.return_value
        mock_real.get_workspace_documents_structure.return_value = mock_root

        workspace = MagicMock()
        backend = OsmoseSourceBackend()
        result = backend.get_workspace_structure(workspace)

    mock_real.get_workspace_documents_structure.assert_called_once_with(workspace)
    assert isinstance(result, SourceFolder)
    assert len(result.children) == 1
    child = result.children[0]
    assert child.name == "Dossier"
    assert len(child.files) == 1
    f = child.files[0]
    assert isinstance(f, SourceFile)
    assert f.name == "rapport"
    assert f.extension == ".pdf"


def test_download_file_calls_real_backend(settings):
    """download_file() delegates to OsmoseRealBackend.download_file()."""
    settings.OSMOSE_BASE_ENDPOINT = ""
    source_file = SourceFile(
        id="f1",
        name="doc",
        extension=".pdf",
        download_url="http://osmose.example.com/files/doc.pdf",
    )

    with patch("core.sources.osmose.backend.OsmoseRealBackend") as mock_real_cls:
        mock_real = mock_real_cls.return_value
        backend = OsmoseSourceBackend()
        backend.download_file(source_file, "/tmp/doc.pdf")

    mock_real.download_file.assert_called_once_with(
        "http://osmose.example.com/files/doc.pdf", "/tmp/doc.pdf"
    )


def test_prepare_export_populates_workspace_members():
    """prepare_export() fetches members from Osmose and stores them on workspace."""
    workspace = MagicMock()
    members = [
        {"name": "Dupont", "firstName": "Jean", "email": "jean@example.com"},
    ]

    with patch("core.sources.osmose.backend.OsmoseRealBackend") as mock_real_cls:
        mock_real = mock_real_cls.return_value
        mock_real.get_members.return_value = members
        backend = OsmoseSourceBackend()
        backend.prepare_export(workspace, "/tmp/workspace")

    mock_real.get_members.assert_called_once_with(workspace)
    assert workspace.members == members
    workspace.save.assert_called_once()
