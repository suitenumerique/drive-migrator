"""Tests for DestinationRegistry — settings-driven destination backend loading."""

from django.test import override_settings

import pytest

from core.backends.destination import AbstractDestinationBackend, DestinationRegistry


class _FakeArchive(AbstractDestinationBackend):
    name = "archive"
    label = "Archive ZIP"

    def export(self, workspace, user, local_folder_path):
        pass


class _FakeDrive(AbstractDestinationBackend):
    name = "drive"
    label = "La Suite Drive"

    def export(self, workspace, user, local_folder_path):
        pass


_ARCHIVE_PATH = f"{_FakeArchive.__module__}._FakeArchive"
_DRIVE_PATH = f"{_FakeDrive.__module__}._FakeDrive"


@pytest.fixture(autouse=True)
def clear_registry_cache():
    """Ensure registry cache is reset between tests."""
    DestinationRegistry.clear_cache()
    yield
    DestinationRegistry.clear_cache()


@override_settings(DESTINATION_BACKENDS=[_ARCHIVE_PATH, _DRIVE_PATH])
def test_get_all_returns_configured_backends():
    backends = DestinationRegistry.get_all()
    assert len(backends) == 2
    assert isinstance(backends[0], _FakeArchive)
    assert isinstance(backends[1], _FakeDrive)


@override_settings(DESTINATION_BACKENDS=[_ARCHIVE_PATH, _DRIVE_PATH])
def test_get_by_name():
    backend = DestinationRegistry.get("drive")
    assert isinstance(backend, _FakeDrive)


@override_settings(DESTINATION_BACKENDS=[_ARCHIVE_PATH])
def test_get_unknown_name_raises():
    with pytest.raises(ValueError, match="unknown"):
        DestinationRegistry.get("unknown")


@override_settings(DESTINATION_BACKENDS=[_ARCHIVE_PATH, _DRIVE_PATH])
def test_get_names_returns_all_names():
    names = DestinationRegistry.get_names()
    assert names == ["archive", "drive"]


@override_settings(DESTINATION_BACKENDS=[_ARCHIVE_PATH])
def test_get_all_is_cached():
    first = DestinationRegistry.get_all()
    second = DestinationRegistry.get_all()
    assert first is second


@override_settings(DESTINATION_BACKENDS=[_ARCHIVE_PATH])
def test_clear_cache_resets_registry():
    first = DestinationRegistry.get_all()
    DestinationRegistry.clear_cache()
    second = DestinationRegistry.get_all()
    assert first is not second


def test_clear_cache_allows_settings_override():
    """After clear_cache(), get_all() reloads backends from the current settings."""
    with override_settings(DESTINATION_BACKENDS=[_ARCHIVE_PATH]):
        first = DestinationRegistry.get_all()
        assert len(first) == 1
        assert isinstance(first[0], _FakeArchive)

    DestinationRegistry.clear_cache()

    with override_settings(DESTINATION_BACKENDS=[_ARCHIVE_PATH, _DRIVE_PATH]):
        second = DestinationRegistry.get_all()
        assert len(second) == 2
        assert isinstance(second[1], _FakeDrive)
