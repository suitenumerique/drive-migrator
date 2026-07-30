"""Tests for FolderCreator — generic SourceFolder/SourceFile-based implementation."""

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


def test_create_folder_sanitizes_slash_in_folder_name(tmp_path, settings):
    """A source folder name containing a "/" must not be split into sub-paths."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws12")
    folder = SourceFolder(
        name="root",
        children=[SourceFolder(name="GT_Tronc/Socle_communs")],
    )
    backend = _make_backend()

    creator = FolderCreator()
    creator.create_folder(workspace, folder, backend)

    workspace_dir = tmp_path / "workspace_ws12"
    assert not (workspace_dir / "GT_Tronc").exists()
    assert os.path.isdir(workspace_dir / "GT_Tronc-Socle_communs")


def test_create_folder_sanitizes_slash_in_file_name(tmp_path, settings):
    """A source file name containing a "/" must not be split into sub-paths."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws13")
    file = SourceFile(
        id="f1", name="report/final", extension=".pdf", download_url="http://x"
    )
    folder = SourceFolder(
        name="root", children=[SourceFolder(name="sub", files=[file])]
    )
    backend = _make_backend()

    creator = FolderCreator()
    creator.create_folder(workspace, folder, backend)

    called_dest = backend.download_file.call_args[0][1]
    assert called_dest == str(tmp_path / "workspace_ws13" / "sub" / "report-final.pdf")


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


# ---------------------------------------------------------------------------
# __download_folder_files — per-file error handling
# ---------------------------------------------------------------------------


def test_create_folder_continues_after_one_file_download_fails(tmp_path, settings):
    """A single file download failure (e.g. HTTP 403/504) must not abort the rest."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws14")
    file_a = SourceFile(
        id="f1", name="broken", extension=".docx", download_url="http://x/broken.docx"
    )
    file_b = SourceFile(
        id="f2", name="ok", extension=".pdf", download_url="http://x/ok.pdf"
    )
    folder = SourceFolder(name="root", files=[file_a, file_b])
    backend = _make_backend()
    backend.download_file.side_effect = [RuntimeError("403 Forbidden"), None]

    creator = FolderCreator()
    creator.create_folder(workspace, folder, backend)  # must not raise

    assert backend.download_file.call_count == 2


def test_create_folder_records_failed_downloads(tmp_path, settings):
    """Failed downloads are tracked on creator.failed_files with name, path and error."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws15")
    file_a = SourceFile(
        id="f1", name="broken", extension=".docx", download_url="http://x/broken.docx"
    )
    folder = SourceFolder(name="root", files=[file_a])
    backend = _make_backend()
    backend.download_file.side_effect = RuntimeError("403 Forbidden")

    creator = FolderCreator()
    creator.create_folder(workspace, folder, backend)

    assert len(creator.failed_files) == 1
    assert creator.failed_files[0]["name"] == "broken.docx"
    assert creator.failed_files[0]["path"] == "broken.docx"
    assert "403 Forbidden" in creator.failed_files[0]["error"]


def test_create_folder_records_failed_download_path_relative_to_workspace_root(
    tmp_path, settings
):
    """A failed download nested in subfolders records a path relative to the workspace root."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws18")
    file_a = SourceFile(
        id="f1", name="broken", extension=".docx", download_url="http://x/broken.docx"
    )
    folder = SourceFolder(
        name="root",
        children=[
            SourceFolder(
                name="level1", children=[SourceFolder(name="level2", files=[file_a])]
            )
        ],
    )
    backend = _make_backend()
    backend.download_file.side_effect = RuntimeError("403 Forbidden")

    creator = FolderCreator()
    creator.create_folder(workspace, folder, backend)

    assert creator.failed_files[0]["path"] == os.path.join(
        "level1", "level2", "broken.docx"
    )


def test_create_folder_does_not_count_failed_download_as_success(tmp_path, settings):
    """A failed download must not increment files_success."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws16")
    file_a = SourceFile(
        id="f1", name="broken", extension=".pdf", download_url="http://x"
    )
    folder = SourceFolder(name="root", files=[file_a])
    backend = _make_backend()
    backend.download_file.side_effect = RuntimeError("boom")

    creator = FolderCreator()
    creator.create_folder(workspace, folder, backend)

    assert creator.files_success == 0


def test_create_folder_succeeding_downloads_are_not_reported_as_failed(
    tmp_path, settings
):
    """A file that downloads fine must not appear in failed_files."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws17")
    file_a = SourceFile(id="f1", name="ok", extension=".pdf", download_url="http://x")
    folder = SourceFolder(name="root", files=[file_a])
    backend = _make_backend()

    creator = FolderCreator()
    creator.create_folder(workspace, folder, backend)

    assert not creator.failed_files
    assert creator.files_success == 1


def test_failed_download_removes_partial_file(tmp_path, settings):
    """A download that writes partial bytes then raises must not leave a partial
    file behind, while other files are still downloaded."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws19")
    file_broken = SourceFile(
        id="f1", name="broken", extension=".docx", download_url="http://x/broken.docx"
    )
    file_ok = SourceFile(
        id="f2", name="ok", extension=".pdf", download_url="http://x/ok.pdf"
    )
    folder = SourceFolder(name="root", files=[file_broken, file_ok])
    backend = _make_backend()

    def download_file(_file, destination):
        if destination.endswith("broken.docx"):
            with open(destination, "wb") as fh:
                fh.write(b"partial-content")
            raise RuntimeError("connection reset")
        with open(destination, "wb") as fh:
            fh.write(b"full-content")

    backend.download_file.side_effect = download_file

    creator = FolderCreator()
    creator.create_folder(workspace, folder, backend)

    workspace_dir = tmp_path / "workspace_ws19"
    assert not (workspace_dir / "broken.docx").exists()
    assert (workspace_dir / "ok.pdf").exists()


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
