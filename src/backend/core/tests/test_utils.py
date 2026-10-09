"""Tests for core.utils."""

import pytest

from core.utils import sanitize_path_component


@pytest.mark.parametrize(
    "name,expected",
    [
        ("report", "report"),
        ("GT/Socle", "GT-Socle"),
        ("../escape", "..-escape"),
        ("..", "--"),
        (".", "-"),
        ("...", "..."),
        (".hidden", ".hidden"),
        ("v1.2", "v1.2"),
    ],
)
def test_sanitize_path_component(name, expected):
    """A name never splits into sub-paths nor designates the current or parent
    directory, which would write outside of its folder."""
    assert sanitize_path_component(name) == expected
