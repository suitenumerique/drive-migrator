"""Tests for the paths of the migrated files in the bucket Resana imports from."""

from core.destinations.resana.paths import resana_dir_renames, resana_path

LONG = "x" * 60
CUT = "x" * 57


def test_short_folders_keep_their_name():
    """Folders within the limit are not renamed."""
    assert not resana_dir_renames(["docs", "docs/2026"])


def test_long_folders_are_truncated():
    """Resana refuses folder names over 57 characters."""
    assert resana_dir_renames([LONG]) == {LONG: CUT}


def test_truncated_folders_get_a_counter_when_they_collide():
    """Siblings ending the same past the limit stay apart."""
    first, second = CUT + "aaa", CUT + "bbb"

    assert resana_dir_renames([first, second]) == {first: CUT, second: CUT + "1"}


def test_truncated_folders_never_take_the_name_of_a_short_sibling():
    """A folder already named like the truncation keeps its name, wherever it
    is in the plan."""
    assert resana_dir_renames([LONG, CUT]) == {LONG: CUT + "1"}


def test_folders_of_other_parents_do_not_collide():
    """Only siblings collide."""
    renames = resana_dir_renames(["a", f"a/{LONG}", "b", f"b/{LONG}"])

    assert renames == {f"a/{LONG}": f"a/{CUT}", f"b/{LONG}": f"b/{CUT}"}


def test_sub_folders_of_a_renamed_folder_follow_it():
    """A renamed folder carries its sub-folders, truncated ones included."""
    renames = resana_dir_renames([LONG, f"{LONG}/short", f"{LONG}/short/{LONG}"])

    assert renames == {LONG: CUT, f"{LONG}/short/{LONG}": f"{CUT}/short/{CUT}"}


def test_resana_path_of_a_file_follows_its_folders():
    """A file lands in its folder as renamed, even several levels below it."""
    renames = resana_dir_renames([LONG, f"{LONG}/short"])

    assert resana_path(f"{LONG}/short/a.txt", renames) == f"{CUT}/short/a.txt"


def test_resana_path_keeps_the_files_of_unrenamed_folders():
    """Files outside of renamed folders keep their path."""
    assert resana_path("docs/a.txt", {}) == "docs/a.txt"
    assert resana_path("a.txt", {LONG: CUT}) == "a.txt"
