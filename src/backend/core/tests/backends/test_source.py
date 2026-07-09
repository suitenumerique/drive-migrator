"""Tests for truncate_folder_files() — generic per-workspace file cap."""

from core.backends.source import SourceFile, SourceFolder, truncate_folder_files


def _file(file_id):
    return SourceFile(
        id=file_id, name=file_id, extension=".pdf", download_url="http://x"
    )


def _count_files(folder: SourceFolder) -> int:
    return len(folder.files) + sum(_count_files(child) for child in folder.children)


def test_truncate_returns_false_when_under_limit():
    """truncate_folder_files() leaves the tree untouched when file count is under the limit."""
    folder = SourceFolder(name="root", files=[_file("a"), _file("b")])

    truncated = truncate_folder_files(folder, limit=5)

    assert truncated is False
    assert _count_files(folder) == 2


def test_truncate_returns_false_when_exactly_at_limit():
    """truncate_folder_files() does not report truncation when file count equals the limit."""
    folder = SourceFolder(name="root", files=[_file("a"), _file("b")])

    truncated = truncate_folder_files(folder, limit=2)

    assert truncated is False
    assert _count_files(folder) == 2


def test_truncate_returns_true_and_caps_files_when_over_limit():
    """truncate_folder_files() drops files beyond the limit and reports truncation."""
    folder = SourceFolder(name="root", files=[_file("a"), _file("b"), _file("c")])

    truncated = truncate_folder_files(folder, limit=2)

    assert truncated is True
    assert _count_files(folder) == 2
    assert [f.id for f in folder.files] == ["a", "b"]


def test_truncate_counts_root_files_before_children_preorder():
    """truncate_folder_files() counts a folder's own files before descending into children."""
    child = SourceFolder(name="child", files=[_file("c1"), _file("c2")])
    folder = SourceFolder(name="root", files=[_file("r1")], children=[child])

    truncated = truncate_folder_files(folder, limit=2)

    assert truncated is True
    assert [f.id for f in folder.files] == ["r1"]
    assert [f.id for f in child.files] == ["c1"]


def test_truncate_prunes_children_entirely_past_limit():
    """truncate_folder_files() empties a child folder entirely once the limit is reached."""
    child = SourceFolder(name="child", files=[_file("c1")])
    folder = SourceFolder(name="root", files=[_file("r1")], children=[child])

    truncated = truncate_folder_files(folder, limit=1)

    assert truncated is True
    assert [f.id for f in folder.files] == ["r1"]
    assert not child.files


def test_truncate_recurses_into_multiple_children_in_order():
    """truncate_folder_files() walks sibling folders in list order until the limit is hit."""
    child_a = SourceFolder(name="a", files=[_file("a1")])
    child_b = SourceFolder(name="b", files=[_file("b1")])
    folder = SourceFolder(name="root", children=[child_a, child_b])

    truncated = truncate_folder_files(folder, limit=1)

    assert truncated is True
    assert [f.id for f in child_a.files] == ["a1"]
    assert not child_b.files


def test_truncate_with_empty_tree_returns_false():
    """truncate_folder_files() is a no-op on a tree with no files."""
    folder = SourceFolder(name="root")

    truncated = truncate_folder_files(folder, limit=3)

    assert truncated is False
