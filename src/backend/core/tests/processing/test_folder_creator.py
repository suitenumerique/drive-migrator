"""Tests for FolderCreator — generic SourceFolder/SourceFile-based implementation."""

import os
from unittest.mock import MagicMock

import pytest

from core.backends.source import (
    AbstractSourceBackend,
    SourceFile,
    SourceFolder,
    SourceTokenExpired,
)
from core.factories import WorkspaceFactory
from core.models import MigrationFile, Workspace
from core.processing.folder_creator import FolderCreator
from core.processing.migration_run import create_migration_run, failed_downloads
from core.processing.plan import build_plan

pytestmark = pytest.mark.django_db


def _write_empty_file(_file, destination):
    """Simulate a successful download by creating the destination file."""
    with open(destination, "wb"):
        pass


def _make_backend():
    """Return a minimal AbstractSourceBackend stub whose downloads write the file."""
    backend = MagicMock(spec=AbstractSourceBackend)
    backend.download_file = MagicMock(side_effect=_write_empty_file)
    return backend


def _run(folder):
    """The working rows of a run planned from folder."""
    return create_migration_run(WorkspaceFactory(), "task-1", build_plan(folder))


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
    creator.create_folder(workspace, _run(folder), backend)

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
    creator.create_folder(workspace, _run(folder), backend)

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
    creator.create_folder(workspace, _run(folder), backend)

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
    creator.create_folder(workspace, _run(folder), backend)

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
    result = creator.create_folder(workspace, _run(folder), backend)

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
    creator.create_folder(workspace, _run(folder), backend)

    assert not (workspace_dir / "stale_file.txt").exists()


def test_create_folder_does_not_create_root_folder_as_subdir(tmp_path, settings):
    """The root SourceFolder is virtual — only its children become real directories."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws6")
    folder = SourceFolder(name="virtual-root")
    backend = _make_backend()

    creator = FolderCreator()
    creator.create_folder(workspace, _run(folder), backend)

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
    creator.create_folder(workspace, _run(folder), backend)

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
    creator.create_folder(workspace, _run(folder), backend)

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
    creator.create_folder(workspace, _run(folder), backend)

    called_dest = backend.download_file.call_args[0][1]
    assert called_dest == str(tmp_path / "workspace_ws13" / "sub" / "report-final.pdf")


def _downloaded_paths(backend):
    return [call.args[1] for call in backend.download_file.call_args_list]


def test_create_folder_refuses_the_files_of_a_dot_dot_folder(tmp_path, settings):
    """Files of a source folder named ".." are refused instead of being written
    above the workspace, while the other files are still downloaded."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws30")
    escaping = SourceFile(id="f1", name="x", extension=".txt", download_url="x")
    regular = SourceFile(id="f2", name="y", extension=".txt", download_url="y")
    folder = SourceFolder(
        name="root",
        children=[SourceFolder(name="..", files=[escaping])],
        files=[regular],
    )
    backend = _make_backend()

    run = _run(folder)
    creator = FolderCreator()
    creator.create_folder(workspace, run, backend)

    assert _downloaded_paths(backend) == [str(tmp_path / "workspace_ws30" / "y.txt")]
    assert [failed["id"] for failed in failed_downloads(run)] == ["f1"]
    assert not (tmp_path / "x.txt").exists()


def test_create_folder_refuses_the_subtree_of_a_dot_dot_folder(tmp_path, settings):
    """Sub-folders of a ".." folder are neither created above the workspace nor
    downloaded."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws31")
    file = SourceFile(id="f1", name="x", extension=".txt", download_url="x")
    folder = SourceFolder(
        name="root",
        children=[
            SourceFolder(name="..", children=[SourceFolder(name="evil", files=[file])])
        ],
    )
    backend = _make_backend()

    run = _run(folder)
    creator = FolderCreator()
    creator.create_folder(workspace, run, backend)

    assert not backend.download_file.called
    assert [failed["id"] for failed in failed_downloads(run)] == ["f1"]
    assert not (tmp_path / "evil").exists()


@pytest.mark.parametrize("name", [".", ".."])
def test_create_folder_refuses_a_file_designating_a_folder(tmp_path, settings, name):
    """A source file named "." or ".." is refused instead of overwriting a
    folder."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws32")
    file = SourceFile(id="f1", name=name, extension="", download_url="x")
    folder = SourceFolder(
        name="root", children=[SourceFolder(name="sub", files=[file])]
    )
    backend = _make_backend()

    run = _run(folder)
    creator = FolderCreator()
    creator.create_folder(workspace, run, backend)

    assert not backend.download_file.called
    assert [failed["id"] for failed in failed_downloads(run)] == ["f1"]
    assert os.path.isdir(tmp_path / "workspace_ws32" / "sub")


@pytest.mark.parametrize("name", ["../..", "../../foo.txt", "../sibling.txt"])
def test_create_folder_refuses_a_file_name_with_a_relative_prefix(
    tmp_path, settings, name
):
    """A source file name climbing out of its folder is refused instead of
    being migrated under a sanitized name."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws33")
    file = SourceFile(id="f1", name=name, extension="", download_url="x")
    folder = SourceFolder(
        name="root", children=[SourceFolder(name="sub", files=[file])]
    )
    backend = _make_backend()

    run = _run(folder)
    creator = FolderCreator()
    creator.create_folder(workspace, run, backend)

    assert not backend.download_file.called
    assert [failed["id"] for failed in failed_downloads(run)] == ["f1"]


def test_create_folder_refuses_a_folder_name_with_a_relative_prefix(tmp_path, settings):
    """A source folder name climbing out of its parent is refused with its
    files instead of being created under a sanitized name."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws34")
    file = SourceFile(id="f1", name="x", extension=".txt", download_url="x")
    folder = SourceFolder(
        name="root", children=[SourceFolder(name="../evil", files=[file])]
    )
    backend = _make_backend()

    run = _run(folder)
    creator = FolderCreator()
    creator.create_folder(workspace, run, backend)

    assert not backend.download_file.called
    assert [failed["id"] for failed in failed_downloads(run)] == ["f1"]
    assert os.listdir(tmp_path / "workspace_ws34") == []


def test_create_folder_refuses_the_files_of_an_empty_folder_name(tmp_path, settings):
    """A source folder whose name is empty, e.g. once cleaned up by the Osmose
    backend, is not supported: its files are reported as failed."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws35")
    file = SourceFile(id="f1", name="x", extension=".txt", download_url="x")
    folder = SourceFolder(name="root", children=[SourceFolder(name="", files=[file])])
    backend = _make_backend()

    run = _run(folder)
    creator = FolderCreator()
    creator.create_folder(workspace, run, backend)

    assert not backend.download_file.called
    assert [failed["id"] for failed in failed_downloads(run)] == ["f1"]


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
    creator.create_folder(workspace, _run(folder), backend)

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
    creator.create_folder(workspace, _run(folder), backend)  # must not raise

    assert backend.download_file.call_count == 2


def test_create_folder_stops_when_the_source_token_expired(tmp_path, settings):
    """An expired source token would fail every remaining file the same way:
    the download stops, so the run fails and asks the user to log in again."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws15")
    file_a = SourceFile(
        id="f1", name="first", extension=".pdf", download_url="http://x/first.pdf"
    )
    file_b = SourceFile(
        id="f2", name="second", extension=".pdf", download_url="http://x/second.pdf"
    )
    folder = SourceFolder(name="root", files=[file_a, file_b])
    backend = _make_backend()
    backend.download_file.side_effect = SourceTokenExpired("token expired")

    run = _run(folder)
    with pytest.raises(SourceTokenExpired):
        FolderCreator().create_folder(workspace, run, backend)

    assert backend.download_file.call_count == 1
    assert [failed["name"] for failed in failed_downloads(run)] == ["first.pdf"]


def test_create_folder_records_failed_downloads(tmp_path, settings):
    """Failed downloads are tracked on failed_downloads(run) with name, path and error."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws15")
    file_a = SourceFile(
        id="f1", name="broken", extension=".docx", download_url="http://x/broken.docx"
    )
    folder = SourceFolder(name="root", files=[file_a])
    backend = _make_backend()
    backend.download_file.side_effect = RuntimeError("403 Forbidden")

    run = _run(folder)
    creator = FolderCreator()
    creator.create_folder(workspace, run, backend)

    assert len(failed_downloads(run)) == 1
    assert failed_downloads(run)[0]["name"] == "broken.docx"
    assert failed_downloads(run)[0]["path"] == "broken.docx"
    assert "403 Forbidden" in failed_downloads(run)[0]["error"]


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

    run = _run(folder)
    creator = FolderCreator()
    creator.create_folder(workspace, run, backend)

    assert failed_downloads(run)[0]["path"] == os.path.join(
        "level1", "level2", "broken.docx"
    )


def test_create_folder_does_not_count_failed_download_as_success(tmp_path, settings):
    """A failed download is not recorded as downloaded."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws16")
    file_a = SourceFile(
        id="f1", name="broken", extension=".pdf", download_url="http://x"
    )
    folder = SourceFolder(name="root", files=[file_a])
    backend = _make_backend()
    backend.download_file.side_effect = RuntimeError("boom")

    run = _run(folder)
    creator = FolderCreator()
    creator.create_folder(workspace, run, backend)

    assert run.files.get().status != MigrationFile.Status.DOWNLOADED


def test_create_folder_succeeding_downloads_are_not_reported_as_failed(
    tmp_path, settings
):
    """A file that downloads fine is not listed as failed."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws17")
    file_a = SourceFile(id="f1", name="ok", extension=".pdf", download_url="http://x")
    folder = SourceFolder(name="root", files=[file_a])
    backend = _make_backend()

    run = _run(folder)
    creator = FolderCreator()
    creator.create_folder(workspace, run, backend)

    assert not failed_downloads(run)
    assert run.files.get().status == MigrationFile.Status.DOWNLOADED


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
    creator.create_folder(workspace, _run(folder), backend)

    workspace_dir = tmp_path / "workspace_ws19"
    assert not (workspace_dir / "broken.docx").exists()
    assert (workspace_dir / "ok.pdf").exists()


def test_create_folder_ensures_filename_uniqueness(tmp_path, settings):
    """Files sharing a name in one folder are written side by side, numbered."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws11")
    first = SourceFile(id="f1", name="doc", extension=".pdf", download_url="http://x")
    second = SourceFile(id="f2", name="doc", extension=".pdf", download_url="http://x")
    folder = SourceFolder(
        name="root", children=[SourceFolder(name="sub", files=[first, second])]
    )

    FolderCreator().create_folder(workspace, _run(folder), _make_backend())

    sub_dir = tmp_path / "workspace_ws11" / "sub"
    assert sorted(os.listdir(sub_dir)) == ["doc (1).pdf", "doc.pdf"]


def test_create_folder_records_each_download_on_its_row(tmp_path, settings):
    """Every file of the run gets the outcome of its own download."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws24")
    files = [
        SourceFile(id="f1", name="ok", extension=".txt", download_url="x"),
        SourceFile(id="f2", name="broken", extension=".txt", download_url="x"),
    ]
    backend = _make_backend()

    def download_file(file, destination):
        if file.id == "f2":
            raise RuntimeError("403 Forbidden")
        _write_empty_file(file, destination)

    backend.download_file.side_effect = download_file
    run = _run(SourceFolder(name="root", files=files))

    FolderCreator().create_folder(workspace, run, backend)

    assert [(row.path, row.status) for row in run.files.all()] == [
        ("ok.txt", MigrationFile.Status.DOWNLOADED),
        ("broken.txt", MigrationFile.Status.DOWNLOAD_FAILED),
    ]


def test_create_folder_creates_empty_planned_dirs(tmp_path, settings):
    """Folders holding no file are created too."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws25")
    folder = SourceFolder(name="root", children=[SourceFolder(name="empty")])

    FolderCreator().create_folder(workspace, _run(folder), _make_backend())

    assert os.path.isdir(tmp_path / "workspace_ws25" / "empty")


def test_create_folder_counts_the_bytes_written(tmp_path, settings):
    """bytes_written sums the size of every downloaded file, failed ones excluded."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws23")
    files = [
        SourceFile(id="f1", name="a", extension=".txt", download_url="x"),
        SourceFile(id="f2", name="b", extension=".txt", download_url="x"),
        SourceFile(id="f3", name="broken", extension=".txt", download_url="x"),
    ]
    backend = _make_backend()

    def download_file(file, destination):
        if file.id == "f3":
            raise RuntimeError("403 Forbidden")
        with open(destination, "wb") as fh:
            fh.write(b"x" * (3 if file.id == "f1" else 4))

    backend.download_file.side_effect = download_file

    creator = FolderCreator()
    creator.create_folder(
        workspace, _run(SourceFolder(name="root", files=files)), backend
    )

    assert creator.bytes_written == 7


# ---------------------------------------------------------------------------
# Failed downloads reported on the workspace
# ---------------------------------------------------------------------------


def test_create_folder_records_source_id_on_failed_downloads(tmp_path, settings):
    """Failed downloads carry the source file id."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws21")
    file = SourceFile(id="f1", name="broken", extension=".docx", download_url="x")
    backend = _make_backend()
    backend.download_file.side_effect = RuntimeError("403 Forbidden")

    run = _run(SourceFolder(name="root", files=[file]))
    creator = FolderCreator()
    creator.create_folder(workspace, run, backend)

    assert failed_downloads(run)[0]["id"] == "f1"


def test_create_folder_flags_download_that_wrote_no_file(tmp_path, settings):
    """A download that returns without error but writes nothing is reported as
    failed and not counted as a success."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws22")
    file = SourceFile(id="f1", name="ghost", extension=".pdf", download_url="x")
    backend = _make_backend()
    backend.download_file.side_effect = None

    run = _run(SourceFolder(name="root", files=[file]))
    creator = FolderCreator()
    creator.create_folder(workspace, run, backend)

    assert run.files.get().status != MigrationFile.Status.DOWNLOADED
    assert failed_downloads(run) == [
        {
            "id": "f1",
            "name": "ghost.pdf",
            "path": "ghost.pdf",
            "error": "Download reported success but wrote no file",
        }
    ]


# ---------------------------------------------------------------------------
# Handing each downloaded file over (file-by-file destinations)
# ---------------------------------------------------------------------------


def test_create_folder_hands_each_downloaded_file_over(tmp_path, settings):
    """after_download gets every downloaded file with its local path, and only those."""
    settings.APP_WORK_DIR = str(tmp_path)
    workspace = _make_workspace("ws26")
    files = [
        SourceFile(id="f1", name="ok", extension=".txt", download_url="x"),
        SourceFile(id="f2", name="broken", extension=".txt", download_url="x"),
    ]
    backend = _make_backend()

    def download_file(file, destination):
        if file.id == "f2":
            raise RuntimeError("403 Forbidden")
        _write_empty_file(file, destination)

    backend.download_file.side_effect = download_file
    handed_over = []

    FolderCreator().create_folder(
        workspace,
        _run(SourceFolder(name="root", files=files)),
        backend,
        after_download=lambda row, path: handed_over.append(
            (row.path, path, os.path.isfile(path))
        ),
    )

    assert handed_over == [
        ("ok.txt", str(tmp_path / "workspace_ws26" / "ok.txt"), True)
    ]
