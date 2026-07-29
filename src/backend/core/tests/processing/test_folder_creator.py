"""Tests for FolderCreator — generic SourceFolder/SourceFile-based implementation."""

import json
import os
from unittest.mock import MagicMock, patch

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
    """get_workspace_path() returns APP_WORK_DIR/workspace_<id>."""
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


def test_create_folder_downloads_root_level_files(tmp_path, settings):
    """create_folder() downloads files placed directly at the workspace root."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws3b")
    root_file = SourceFile(
        id="f1", name="readme", extension=".pdf", download_url="http://src/readme.pdf"
    )
    folder = SourceFolder(name="root", files=[root_file])
    backend = _make_backend()

    creator = FolderCreator()
    creator.create_folder(workspace, folder, backend)

    assert backend.download_file.call_count == 1
    called_file = backend.download_file.call_args[0][0]
    assert called_file == root_file


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


def test_create_folder_recurses_into_nested_subfolders(tmp_path, settings):
    """create_folder() recurses into grandchild folders (covers recursive __create_folder call)."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws9")
    folder = SourceFolder(
        name="root",
        children=[
            SourceFolder(
                name="level1",
                children=[SourceFolder(name="level2")],
            )
        ],
    )
    backend = _make_backend()

    creator = FolderCreator()
    creator.create_folder(workspace, folder, backend)

    assert os.path.isdir(tmp_path / "workspace_ws9" / "level1" / "level2")


def test_create_folder_logs_truncated_filename(tmp_path, settings):
    """When a filename is truncated, create_folder() uses the truncated path."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws10")
    long_name = "a" * 200
    file = SourceFile(
        id="f1", name=long_name, extension=".pdf", download_url="http://x"
    )
    folder = SourceFolder(
        name="root", children=[SourceFolder(name="sub", files=[file])]
    )
    backend = _make_backend()

    creator = FolderCreator()
    creator.create_folder(workspace, folder, backend)

    # download_file must have been called with a path shorter than the original
    called_dest = backend.download_file.call_args[0][1]
    original_dest = str(tmp_path / "workspace_ws10" / "sub" / (long_name + ".pdf"))
    assert called_dest != original_dest
    assert len(os.path.basename(called_dest)) < len(long_name + ".pdf")


def test_create_folder_ensures_filename_uniqueness(tmp_path, settings):
    """When ensure_file_uniqueness returns a different path, create_folder() uses it."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws11")
    file = SourceFile(id="f1", name="doc", extension=".pdf", download_url="http://x")
    folder = SourceFolder(
        name="root", children=[SourceFolder(name="sub", files=[file])]
    )
    backend = _make_backend()

    # Simulate a name collision: ensure_file_uniqueness returns a different path
    with patch(
        "core.processing.folder_creator.ensure_file_uniqueness",
        side_effect=lambda p: p.replace("doc.pdf", "doc (1).pdf"),
    ):
        creator = FolderCreator()
        creator.create_folder(workspace, folder, backend)

    called_dest = backend.download_file.call_args[0][1]
    assert "doc (1).pdf" in called_dest


# ---------------------------------------------------------------------------
# create_folder — _file_manifest.json
# ---------------------------------------------------------------------------


def test_create_folder_writes_file_manifest(tmp_path, settings):
    """create_folder() writes _file_manifest.json in the workspace directory."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws-manifest")
    file = SourceFile(id="src-uuid-1", name="doc", extension=".pdf", download_url="x")
    folder = SourceFolder(name="root", files=[file])
    backend = _make_backend()

    creator = FolderCreator()
    local_path = creator.create_folder(workspace, folder, backend)

    assert os.path.exists(os.path.join(local_path, "_file_manifest.json"))


def test_file_manifest_maps_rel_path_to_source_id(tmp_path, settings):
    """_file_manifest.json maps each file's relative path to its source file ID."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws-manifest2")
    file = SourceFile(
        id="src-uuid-42", name="rapport", extension=".pdf", download_url="x"
    )
    folder = SourceFolder(name="root", files=[file])
    backend = _make_backend()

    creator = FolderCreator()
    local_path = creator.create_folder(workspace, folder, backend)

    with open(os.path.join(local_path, "_file_manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest == {"rapport.pdf": "src-uuid-42"}


def test_file_manifest_uses_relative_paths_for_nested_files(tmp_path, settings):
    """Nested files are recorded with their folder-relative path."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws-manifest3")
    file = SourceFile(id="src-nested", name="note", extension=".txt", download_url="x")
    folder = SourceFolder(
        name="root",
        children=[SourceFolder(name="DossierA", files=[file])],
    )
    backend = _make_backend()

    creator = FolderCreator()
    local_path = creator.create_folder(workspace, folder, backend)

    with open(os.path.join(local_path, "_file_manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    assert "DossierA/note.txt" in manifest
    assert manifest["DossierA/note.txt"] == "src-nested"


def test_file_manifest_preserves_source_id_after_rename(tmp_path, settings):
    """Even when ensure_file_uniqueness renames the file, the source ID is preserved."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws-manifest4")
    file = SourceFile(id="src-original", name="doc", extension=".pdf", download_url="x")
    folder = SourceFolder(name="root", files=[file])
    backend = _make_backend()

    with patch(
        "core.processing.folder_creator.ensure_file_uniqueness",
        side_effect=lambda p: p.replace("doc.pdf", "doc (1).pdf"),
    ):
        creator = FolderCreator()
        local_path = creator.create_folder(workspace, folder, backend)

    with open(os.path.join(local_path, "_file_manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest.get("doc (1).pdf") == "src-original"


def test_file_manifest_contains_all_files(tmp_path, settings):
    """_file_manifest.json contains an entry for every downloaded file."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws-manifest5")
    files = [
        SourceFile(id=f"id-{i}", name=f"file{i}", extension=".pdf", download_url="x")
        for i in range(5)
    ]
    folder = SourceFolder(name="root", files=files)
    backend = _make_backend()

    creator = FolderCreator()
    local_path = creator.create_folder(workspace, folder, backend)

    with open(os.path.join(local_path, "_file_manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    assert len(manifest) == 5
