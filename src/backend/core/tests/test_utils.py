"""Tests for core.utils."""

import os

import pytest

from core.utils import ensure_inside_directory, sanitize_path_component


@pytest.mark.parametrize(
    "name,expected",
    [
        ("report", "report"),
        ("GT/Socle", "GT-Socle"),
        ("../escape", "..-escape"),
        ("..", ".."),
        (".", "."),
        ("v1.2", "v1.2"),
    ],
)
def test_sanitize_path_component(name, expected):
    """A name never splits into sub-paths."""
    assert sanitize_path_component(name) == expected


@pytest.mark.parametrize("name", ["report", "v1.2", "...", ".hidden"])
def test_ensure_inside_directory_accepts_a_child(tmp_path, name):
    """A path designating an entry of the directory is accepted."""
    ensure_inside_directory(os.path.join(tmp_path, name), tmp_path)


@pytest.mark.parametrize("name", [".", "..", "../sibling", "sub/../.."])
def test_ensure_inside_directory_rejects_a_path_escaping_the_directory(tmp_path, name):
    """A path resolving to the directory itself or above it is rejected."""
    with pytest.raises(ValueError):
        ensure_inside_directory(os.path.join(tmp_path, name), tmp_path)


def test_ensure_inside_directory_rejects_a_symlink_escaping_the_directory(tmp_path):
    """A symlink pointing outside of the directory is rejected."""
    directory = tmp_path / "workspace"
    directory.mkdir()
    (directory / "link").symlink_to(tmp_path)

    with pytest.raises(ValueError):
        ensure_inside_directory(directory / "link" / "x.txt", directory)


def test_ensure_inside_directory_accepts_a_directory_behind_a_symlink(tmp_path):
    """The directory itself may be reached through a symlink."""
    (tmp_path / "real").mkdir()
    (tmp_path / "alias").symlink_to(tmp_path / "real")

    ensure_inside_directory(tmp_path / "alias" / "x.txt", tmp_path / "alias")
