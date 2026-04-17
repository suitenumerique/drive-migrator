"""Tests for FileSystemSourceBackend."""
# pylint: disable=redefined-outer-name

import os
from unittest.mock import MagicMock

import pytest

from core.backends.source import AbstractSourceBackend, SourceFolder, SourceWorkspace
from core.sources.filesystem import FileSystemSourceBackend

USER_EMAIL = "user@example.com"


@pytest.fixture
def user():
    u = MagicMock()
    u.email = USER_EMAIL
    return u


@pytest.fixture
def source_root(tmp_path):
    """Create a temporary filesystem source root with sample workspaces under a user dir."""
    user_dir = tmp_path / USER_EMAIL
    user_dir.mkdir()

    ws_a = user_dir / "Workspace A"
    ws_a.mkdir()
    (ws_a / "Folder 1").mkdir()
    (ws_a / "Folder 1" / "document.pdf").write_bytes(b"pdf content")
    (ws_a / "Folder 1" / "rapport.docx").write_bytes(b"docx content")
    (ws_a / "Folder 2").mkdir()
    (ws_a / "Folder 2" / "image.png").write_bytes(b"png content")

    ws_b = user_dir / "Workspace B"
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


def test_get_workspaces_returns_subdirectories(backend, user):
    """get_workspaces() returns one SourceWorkspace per immediate subdirectory."""
    workspaces = backend.get_workspaces(user=user)
    assert len(workspaces) == 2
    titles = {ws.title for ws in workspaces}
    assert titles == {"Workspace A", "Workspace B"}


def test_get_workspaces_uses_path_as_id(backend, source_root, user):
    """Each workspace id is the absolute path to its directory."""
    workspaces = backend.get_workspaces(user=user)
    for ws in workspaces:
        assert os.path.isabs(ws.id)
        assert os.path.isdir(ws.id)
        assert ws.id == os.path.join(str(source_root), USER_EMAIL, ws.title)


def test_get_workspaces_returns_source_workspace_instances(backend, user):
    """get_workspaces() returns SourceWorkspace objects."""
    workspaces = backend.get_workspaces(user=user)
    for ws in workspaces:
        assert isinstance(ws, SourceWorkspace)


def test_get_workspaces_returns_empty_when_no_user(settings, tmp_path):
    """get_workspaces() returns an empty list when user is None."""
    (tmp_path / "a_workspace").mkdir()
    settings.FILESYSTEM_SOURCE_ROOT = str(tmp_path)
    fs_backend = FileSystemSourceBackend()
    assert fs_backend.get_workspaces(user=None) == []


def test_get_workspaces_returns_empty_when_no_user_directory(settings, tmp_path):
    """get_workspaces() returns an empty list when {root}/{user.email}/ does not exist."""
    user = MagicMock()
    user.email = "unknown@example.com"
    (tmp_path / "WS-A").mkdir()
    settings.FILESYSTEM_SOURCE_ROOT = str(tmp_path)
    fs_backend = FileSystemSourceBackend()
    assert fs_backend.get_workspaces(user=user) == []


def test_get_workspace_structure_flat(backend, user):
    """get_workspace_structure() returns a SourceFolder with files at root level."""
    ws = backend.get_workspaces(user=user)
    ws_b = next(w for w in ws if w.title == "Workspace B")

    workspace = type("Workspace", (), {"source_id": ws_b.id})()
    root = backend.get_workspace_structure(workspace)

    assert isinstance(root, SourceFolder)
    assert len(root.files) == 1
    assert root.files[0].name == "fichier"
    assert root.files[0].extension == ".txt"


def test_get_workspace_structure_nested(backend, user):
    """get_workspace_structure() recursively builds nested folders."""
    ws = backend.get_workspaces(user=user)
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


def test_download_file_copies_to_destination(backend, user, tmp_path):
    """download_file() copies the file content to the given destination path."""
    ws = backend.get_workspaces(user=user)
    ws_b = next(w for w in ws if w.title == "Workspace B")

    workspace = type("Workspace", (), {"source_id": ws_b.id})()
    root = backend.get_workspace_structure(workspace)

    dest = str(tmp_path / "output.txt")
    backend.download_file(root.files[0], dest)

    assert os.path.exists(dest)
    with open(dest, "rb") as f:
        assert f.read() == b"text content"


def test_download_file_uses_download_url(backend, user):
    """download_file() uses file.download_url as the source path."""
    ws = backend.get_workspaces(user=user)
    ws_b = next(w for w in ws if w.title == "Workspace B")

    workspace = type("Workspace", (), {"source_id": ws_b.id})()
    root = backend.get_workspace_structure(workspace)

    assert os.path.isabs(root.files[0].download_url)
    assert os.path.exists(root.files[0].download_url)


# ---------------------------------------------------------------------------
# get_workspaces() — user directory filtering
# ---------------------------------------------------------------------------


def test_get_workspaces_uses_user_email_subdirectory(settings, tmp_path):
    """Only workspaces inside {root}/{user.email}/ are returned."""
    user = MagicMock()
    user.email = "admin@example.com"
    user_dir = tmp_path / "admin@example.com"
    user_dir.mkdir()
    (user_dir / "WS-1").mkdir()
    (user_dir / "WS-2").mkdir()
    (tmp_path / "Other").mkdir()  # at root — must be ignored
    settings.FILESYSTEM_SOURCE_ROOT = str(tmp_path)
    fs_backend = FileSystemSourceBackend()
    workspaces = fs_backend.get_workspaces(user=user)
    titles = {ws.title for ws in workspaces}
    assert titles == {"WS-1", "WS-2"}


# ---------------------------------------------------------------------------
# get_workspace_structure() — _users.csv exclusion
# ---------------------------------------------------------------------------


def test_get_workspace_structure_excludes_users_csv(settings, tmp_path):
    """_users.csv at the workspace root is excluded from the file tree."""
    ws_dir = tmp_path / "WS"
    ws_dir.mkdir()
    (ws_dir / "_users.csv").write_text("Dupont,Jean,jean@example.com")
    (ws_dir / "document.pdf").write_bytes(b"content")
    settings.FILESYSTEM_SOURCE_ROOT = str(tmp_path)
    fs_backend = FileSystemSourceBackend()
    workspace = type("Workspace", (), {"source_id": str(ws_dir)})()
    root = fs_backend.get_workspace_structure(workspace)
    file_names = {f.name_with_extension for f in root.files}
    assert "_users.csv" not in file_names
    assert "document.pdf" in file_names


# ---------------------------------------------------------------------------
# prepare_export()
# ---------------------------------------------------------------------------


def test_prepare_export_populates_workspace_members(settings, tmp_path):
    """prepare_export() parses _users.csv and stores members on workspace."""
    ws_dir = tmp_path / "WS"
    ws_dir.mkdir()
    (ws_dir / "_users.csv").write_text(
        "Dupont,Jean,jean@example.com\nMartin,Alice,alice@example.com\n"
    )
    settings.FILESYSTEM_SOURCE_ROOT = str(tmp_path)
    fs_backend = FileSystemSourceBackend()
    workspace = MagicMock()
    workspace.source_id = str(ws_dir)
    fs_backend.prepare_export(workspace, str(tmp_path / "export"))
    assert workspace.members == [
        {"name": "Dupont", "firstName": "Jean", "email": "jean@example.com"},
        {"name": "Martin", "firstName": "Alice", "email": "alice@example.com"},
    ]
    workspace.save.assert_called_once()


def test_prepare_export_does_nothing_when_no_users_csv(settings, tmp_path):
    """prepare_export() does not touch workspace.members when _users.csv is absent."""
    ws_dir = tmp_path / "WS"
    ws_dir.mkdir()
    settings.FILESYSTEM_SOURCE_ROOT = str(tmp_path)
    fs_backend = FileSystemSourceBackend()
    workspace = MagicMock()
    workspace.source_id = str(ws_dir)
    fs_backend.prepare_export(workspace, str(tmp_path / "export"))  # must not raise
    workspace.save.assert_not_called()
