"""Tests for FileSystemSourceBackend."""

import os

import pytest

from core.backends.source import AbstractSourceBackend, SourceFolder, SourceWorkspace
from core.sources.filesystem import FileSystemSourceBackend


@pytest.fixture
def source_root(tmp_path):
    """Create a temporary filesystem source root with sample workspaces."""
    ws_a = tmp_path / "Workspace A"
    ws_a.mkdir()
    (ws_a / "Folder 1").mkdir()
    (ws_a / "Folder 1" / "document.pdf").write_bytes(b"pdf content")
    (ws_a / "Folder 1" / "rapport.docx").write_bytes(b"docx content")
    (ws_a / "Folder 2").mkdir()
    (ws_a / "Folder 2" / "image.png").write_bytes(b"png content")

    ws_b = tmp_path / "Workspace B"
    ws_b.mkdir()
    (ws_b / "fichier.txt").write_bytes(b"text content")

    return tmp_path


@pytest.fixture
def backend(source_root, settings):
    settings.FILESYSTEM_SOURCE_ROOT = str(source_root)
    return FileSystemSourceBackend()


def test_implements_abstract_source_backend():
    """FileSystemSourceBackend must be a concrete implementation of AbstractSourceBackend."""
    assert issubclass(FileSystemSourceBackend, AbstractSourceBackend)


def test_source_type_is_filesystem():
    """source_type must be 'filesystem'."""
    assert FileSystemSourceBackend.source_type == "filesystem"


def test_get_workspaces_returns_subdirectories(backend, source_root):
    """get_workspaces() returns one SourceWorkspace per immediate subdirectory."""
    workspaces = backend.get_workspaces(user=None)
    assert len(workspaces) == 2
    titles = {ws.title for ws in workspaces}
    assert titles == {"Workspace A", "Workspace B"}


def test_get_workspaces_uses_path_as_id(backend, source_root):
    """Each workspace id is the absolute path to its directory."""
    workspaces = backend.get_workspaces(user=None)
    for ws in workspaces:
        assert os.path.isabs(ws.id)
        assert os.path.isdir(ws.id)
        assert ws.id == os.path.join(str(source_root), ws.title)


def test_get_workspaces_returns_source_workspace_instances(backend):
    """get_workspaces() returns SourceWorkspace objects."""
    workspaces = backend.get_workspaces(user=None)
    for ws in workspaces:
        assert isinstance(ws, SourceWorkspace)


def test_get_workspaces_empty_root(settings, tmp_path):
    """get_workspaces() returns an empty list when the root has no subdirectories."""
    settings.FILESYSTEM_SOURCE_ROOT = str(tmp_path)
    backend = FileSystemSourceBackend()
    assert backend.get_workspaces(user=None) == []


def test_get_workspaces_ignores_files_at_root(settings, tmp_path):
    """get_workspaces() ignores plain files at the root level."""
    (tmp_path / "some_file.txt").write_bytes(b"ignored")
    (tmp_path / "a_workspace").mkdir()
    settings.FILESYSTEM_SOURCE_ROOT = str(tmp_path)
    backend = FileSystemSourceBackend()
    workspaces = backend.get_workspaces(user=None)
    assert len(workspaces) == 1


def test_get_workspace_structure_flat(backend, source_root):
    """get_workspace_structure() returns a SourceFolder with files at root level."""
    ws = backend.get_workspaces(user=None)
    ws_b = next(w for w in ws if w.title == "Workspace B")

    workspace = type("Workspace", (), {"source_id": ws_b.id})()
    root = backend.get_workspace_structure(workspace)

    assert isinstance(root, SourceFolder)
    assert len(root.files) == 1
    assert root.files[0].name == "fichier"
    assert root.files[0].extension == ".txt"


def test_get_workspace_structure_nested(backend, source_root):
    """get_workspace_structure() recursively builds nested folders."""
    ws = backend.get_workspaces(user=None)
    ws_a = next(w for w in ws if w.title == "Workspace A")

    workspace = type("Workspace", (), {"source_id": ws_a.id})()
    root = backend.get_workspace_structure(workspace)

    assert len(root.children) == 2
    child_names = {c.name for c in root.children}
    assert child_names == {"Folder 1", "Folder 2"}

    folder1 = next(c for c in root.children if c.name == "Folder 1")
    assert len(folder1.files) == 2
    file_names = {f.name_with_extension for f in folder1.files}
    assert file_names == {"document.pdf", "rapport.docx"}

    folder2 = next(c for c in root.children if c.name == "Folder 2")
    assert len(folder2.files) == 1
    assert folder2.files[0].name_with_extension == "image.png"


def test_download_file_copies_to_destination(backend, source_root, tmp_path):
    """download_file() copies the file content to the given destination path."""
    ws = backend.get_workspaces(user=None)
    ws_b = next(w for w in ws if w.title == "Workspace B")

    workspace = type("Workspace", (), {"source_id": ws_b.id})()
    root = backend.get_workspace_structure(workspace)

    dest = str(tmp_path / "output.txt")
    backend.download_file(root.files[0], dest)

    assert os.path.exists(dest)
    with open(dest, "rb") as f:
        assert f.read() == b"text content"


def test_download_file_uses_download_url(backend, source_root):
    """download_file() uses file.download_url as the source path."""
    ws = backend.get_workspaces(user=None)
    ws_b = next(w for w in ws if w.title == "Workspace B")

    workspace = type("Workspace", (), {"source_id": ws_b.id})()
    root = backend.get_workspace_structure(workspace)

    assert os.path.isabs(root.files[0].download_url)
    assert os.path.exists(root.files[0].download_url)
