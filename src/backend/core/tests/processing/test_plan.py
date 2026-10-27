"""Tests for the in-memory migration plan."""

import os

import pytest

from core.backends.source import SourceFile, SourceFolder
from core.processing.plan import MigrationPlan, PlannedFile, build_plan


def _file(file_id, name, extension=".pdf"):
    return SourceFile(id=file_id, name=name, extension=extension, download_url="x")


def _paths(plan: MigrationPlan):
    return [planned.path for planned in plan.files]


def test_build_plan_of_an_empty_root():
    """An empty root plans nothing: the root folder itself is virtual."""
    assert build_plan(SourceFolder(name="root")) == MigrationPlan(dirs=[], files=[])


def test_build_plan_keeps_the_source_file():
    """Each planned file carries the source file it is downloaded from."""
    readme = _file("f1", "readme")

    plan = build_plan(SourceFolder(name="root", files=[readme]))

    assert plan.files == [PlannedFile(source=readme, path="readme.pdf")]


def test_build_plan_lists_dirs_parents_first():
    """Every folder is planned, a parent before its children."""
    root = SourceFolder(
        name="root",
        children=[
            SourceFolder(name="a", children=[SourceFolder(name="b")]),
            SourceFolder(name="c"),
        ],
    )

    assert build_plan(root).dirs == ["a", os.path.join("a", "b"), "c"]


def test_build_plan_orders_files_like_the_download():
    """A folder's subfolders are planned before its own files, recursively."""
    root = SourceFolder(
        name="root",
        files=[_file("f1", "root_file")],
        children=[
            SourceFolder(
                name="a",
                files=[_file("f2", "a_file")],
                children=[SourceFolder(name="b", files=[_file("f3", "b_file")])],
            )
        ],
    )

    assert _paths(build_plan(root)) == [
        os.path.join("a", "b", "b_file.pdf"),
        os.path.join("a", "a_file.pdf"),
        "root_file.pdf",
    ]


def test_build_plan_sanitizes_slashes():
    """A "/" in a folder or file name never creates an extra level."""
    root = SourceFolder(
        name="root",
        children=[
            SourceFolder(name="GT/Socle", files=[_file("f1", "report/final")]),
        ],
    )

    plan = build_plan(root)

    assert plan.dirs == ["GT-Socle"]
    assert _paths(plan) == [os.path.join("GT-Socle", "report-final.pdf")]


def test_build_plan_truncates_long_names():
    """Long folder and file names are truncated, keeping the file extension."""
    root = SourceFolder(
        name="root",
        children=[SourceFolder(name="d" * 250, files=[_file("f1", "f" * 250)])],
    )

    plan = build_plan(root)

    folder, filename = os.path.split(_paths(plan)[0])
    assert plan.dirs == [folder]
    assert len(folder) == 190
    assert len(filename) <= 190
    assert filename.endswith(".pdf")


def test_build_plan_numbers_duplicate_file_names():
    """Files sharing a name in one folder get " (n)" before their extension."""
    root = SourceFolder(
        name="root", files=[_file("f1", "doc"), _file("f2", "doc"), _file("f3", "doc")]
    )

    assert _paths(build_plan(root)) == ["doc.pdf", "doc (1).pdf", "doc (2).pdf"]


def test_build_plan_numbers_names_that_collide_once_sanitized():
    """Two names that only differ before sanitization still get distinct paths."""
    root = SourceFolder(
        name="root", files=[_file("f1", "a/b", ".txt"), _file("f2", "a-b", ".txt")]
    )

    assert _paths(build_plan(root)) == ["a-b.txt", "a-b (1).txt"]


def test_build_plan_numbers_a_file_named_like_a_sibling_folder():
    """A file can't take the name of a folder planned in the same place."""
    root = SourceFolder(
        name="root",
        files=[_file("f1", "notes", "")],
        children=[SourceFolder(name="notes")],
    )

    assert _paths(build_plan(root)) == ["notes (1)"]


def test_build_plan_merges_sibling_folders_sharing_a_name():
    """Same-named sibling folders share one folder, whose files stay unique."""
    root = SourceFolder(
        name="root",
        children=[
            SourceFolder(name="docs", files=[_file("f1", "doc")]),
            SourceFolder(name="docs", files=[_file("f2", "doc")]),
        ],
    )

    plan = build_plan(root)

    assert plan.dirs == ["docs"]
    assert _paths(plan) == [
        os.path.join("docs", "doc.pdf"),
        os.path.join("docs", "doc (1).pdf"),
    ]


def _refused_ids(plan: MigrationPlan):
    return [refused.source.id for refused in plan.refused]


@pytest.mark.parametrize("name", [".", "..", "../..", "../../foo.txt", ""])
def test_build_plan_refuses_a_file_name_escaping_its_folder(name):
    """A file name resolving to its folder or above it is refused, not planned."""
    root = SourceFolder(
        name="root",
        children=[SourceFolder(name="sub", files=[_file("f1", name, "")])],
    )

    plan = build_plan(root)

    assert not plan.files
    assert _refused_ids(plan) == ["f1"]
    assert plan.dirs == ["sub"]


@pytest.mark.parametrize("name", ["..", "../evil", ""])
def test_build_plan_refuses_the_subtree_of_a_folder_escaping_its_parent(name):
    """Every file under a folder escaping its parent is refused, and none of its
    folders is planned."""
    root = SourceFolder(
        name="root",
        files=[_file("f1", "kept")],
        children=[
            SourceFolder(
                name=name,
                files=[_file("f2", "x")],
                children=[SourceFolder(name="evil", files=[_file("f3", "y")])],
            )
        ],
    )

    plan = build_plan(root)

    assert not plan.dirs
    assert _paths(plan) == ["kept.pdf"]
    assert _refused_ids(plan) == ["f2", "f3"]


def test_build_plan_explains_a_refusal():
    """A refused file keeps its sanitized path and the reason of its refusal."""
    plan = build_plan(SourceFolder(name="root", files=[_file("f1", "..", "")]))

    assert [refused.path for refused in plan.refused] == [".."]
    assert "is not inside" in plan.refused[0].error


@pytest.mark.parametrize("name", ["...", ".hidden", "v1.2", "a/../b"])
def test_build_plan_keeps_dotted_names_inside_their_folder(name):
    """Names with dots that stay inside their folder are planned as usual."""
    plan = build_plan(SourceFolder(name="root", files=[_file("f1", name, "")]))

    assert not plan.refused
    assert len(plan.files) == 1
