"""Tests for FolderCreator — generic SourceFolder/SourceFile-based implementation."""

import os
from unittest.mock import MagicMock, call, patch

import pytest

from core.backends.source import AbstractSourceBackend, SourceFile, SourceFolder
from core.models import Workspace
from core.processing.folder_creator import FolderCreator


def _make_backend():
    """Return a minimal AbstractSourceBackend stub."""
    backend = MagicMock(spec=AbstractSourceBackend)
    backend.download_file = MagicMock()
    return backend


def _make_workspace(workspace_id="ws-1"):
    workspace = MagicMock(spec=Workspace)
    workspace.id = workspace_id
    return workspace


# ---------------------------------------------------------------------------
# get_workspace_path
# ---------------------------------------------------------------------------


def test_get_workspace_path_uses_work_dir(settings):
    settings.APP_WORK_DIR = "/tmp/work"
    workspace = _make_workspace("abc")
    creator = FolderCreator()
    assert creator.get_workspace_path(workspace) == "/tmp/work/workspace_abc"


# ---------------------------------------------------------------------------
# create_folder — folder/file structure creation
# ---------------------------------------------------------------------------


def test_create_folder_creates_root_dir(tmp_path, settings):
    """create_folder() creates the workspace root directory."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws1")
    folder = SourceFolder(name="root")
    backend = _make_backend()

    creator = FolderCreator()
    creator.create_folder(workspace, folder, backend)

    assert os.path.isdir(tmp_path / "workspace_ws1")


def test_create_folder_creates_child_dirs(tmp_path, settings):
    """create_folder() creates subdirectories for child folders."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws2")
    folder = SourceFolder(
        name="root",
        children=[SourceFolder(name="subdir")],
    )
    backend = _make_backend()

    creator = FolderCreator()
    creator.create_folder(workspace, folder, backend)

    assert os.path.isdir(tmp_path / "workspace_ws2" / "subdir")


def test_create_folder_downloads_files(tmp_path, settings):
    """create_folder() calls source_backend.download_file() for each file."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws3")
    file_a = SourceFile(
        id="f1", name="doc", extension=".pdf", download_url="http://src/doc.pdf"
    )
    file_b = SourceFile(
        id="f2", name="img", extension=".png", download_url="http://src/img.png"
    )
    folder = SourceFolder(
        name="root", children=[SourceFolder(name="sub", files=[file_a, file_b])]
    )
    backend = _make_backend()

    creator = FolderCreator()
    creator.create_folder(workspace, folder, backend)

    assert backend.download_file.call_count == 2
    calls = [c[0] for c in backend.download_file.call_args_list]
    downloaded_files = [c[0] for c in calls]
    assert file_a in downloaded_files
    assert file_b in downloaded_files


def test_create_folder_returns_local_path(tmp_path, settings):
    """create_folder() returns the path of the created workspace directory."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws4")
    folder = SourceFolder(name="root")
    backend = _make_backend()

    creator = FolderCreator()
    result = creator.create_folder(workspace, folder, backend)

    assert result == str(tmp_path / "workspace_ws4")


def test_create_folder_clears_existing_dir(tmp_path, settings):
    """create_folder() removes any pre-existing workspace directory before recreating it."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws5")
    workspace_dir = tmp_path / "workspace_ws5"
    workspace_dir.mkdir()
    (workspace_dir / "stale_file.txt").write_text("old")

    folder = SourceFolder(name="root")
    backend = _make_backend()

    creator = FolderCreator()
    creator.create_folder(workspace, folder, backend)

    assert not (workspace_dir / "stale_file.txt").exists()


def test_create_folder_does_not_create_root_folder_as_subdir(tmp_path, settings):
    """The root SourceFolder is virtual — only its children become real directories."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws6")
    folder = SourceFolder(name="virtual-root")
    backend = _make_backend()

    creator = FolderCreator()
    creator.create_folder(workspace, folder, backend)

    workspace_dir = tmp_path / "workspace_ws6"
    assert os.path.isdir(workspace_dir)
    # No subdirectory named "virtual-root" should exist
    assert not os.path.isdir(workspace_dir / "virtual-root")


# ---------------------------------------------------------------------------
# delete_folder
# ---------------------------------------------------------------------------


def test_delete_folder_removes_existing_dir(tmp_path, settings):
    """delete_folder() removes the workspace directory if it exists."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws7")
    workspace_dir = tmp_path / "workspace_ws7"
    workspace_dir.mkdir()
    (workspace_dir / "file.txt").write_text("content")

    creator = FolderCreator()
    creator.delete_folder(workspace)

    assert not workspace_dir.exists()


def test_delete_folder_is_safe_when_missing(tmp_path, settings):
    """delete_folder() does nothing if the directory does not exist (no error)."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws8")

    creator = FolderCreator()
    creator.delete_folder(workspace)  # must not raise
