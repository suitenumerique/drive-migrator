"""Tests for the archive download: zip streamed from the S3 manifest."""

import io
import zipfile
from unittest.mock import patch

from django.core.cache import cache

import pytest

from core.destinations.archive.download import (
    acquire_download_slot,
    archive_stream,
    release_download_slot,
)


@pytest.fixture(name="bucket")
def fixture_bucket():
    """S3 objects behind ArchiveManager, as {key: bytes}, and the keys read."""
    objects = {}
    read = []
    with patch("core.destinations.archive.download.ArchiveManager") as mock_cls:

        def iter_object(key):
            read.append(key)
            content = objects[key]
            yield content[:2]
            yield content[2:]

        mock_cls.return_value.iter_object.side_effect = iter_object
        mock_cls.return_value.objects = objects
        mock_cls.return_value.read = read
        yield mock_cls.return_value


@pytest.fixture(autouse=True)
def local_cache(settings):
    """Count the download slots in a cache of this test only."""
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    cache.clear()


def _manifest(bucket, files, dirs=()):
    entries = []
    for index, (path, content) in enumerate(files.items()):
        bucket.objects[f"k{index}"] = content
        entries.append({"path": path, "key": f"k{index}", "size": len(content)})
    return {"dirs": list(dirs), "files": entries}


def test_archive_stream_zips_the_files_and_folders_of_the_manifest(bucket):
    """The zip holds each file at its path, and the empty folders."""
    manifest = _manifest(
        bucket,
        {"a.txt": b"hello", "docs/b.txt": b"world"},
        dirs=["docs", "empty"],
    )

    content = b"".join(archive_stream(manifest))

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert archive.read("a.txt") == b"hello"
        assert archive.read("docs/b.txt") == b"world"
        assert "empty/" in archive.namelist()
        assert archive.testzip() is None


def test_archive_stream_announces_its_exact_size(bucket):
    """The size is known before streaming, for the Content-Length header."""
    manifest = _manifest(bucket, {"a.txt": b"hello", "b.txt": b""})

    stream = archive_stream(manifest)

    assert len(stream) == len(b"".join(stream))


def test_archive_stream_stores_the_files_uncompressed(bucket):
    """Files are stored, not deflated: they are often already compressed."""
    manifest = _manifest(bucket, {"a.txt": b"aaaaaaaaaaaaaaaaaaaa"})

    content = b"".join(archive_stream(manifest))

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert archive.getinfo("a.txt").compress_type == zipfile.ZIP_STORED


def test_archive_stream_keeps_accented_names(bucket):
    """Accented names are flagged UTF-8, so strict unzip tools read them
    right (see issue #166)."""
    manifest = _manifest(bucket, {"Réunion été.txt": b"x"})

    content = b"".join(archive_stream(manifest))

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        info = archive.getinfo("Réunion été.txt")
        assert info.flag_bits & 0x800


def test_archive_stream_reads_each_object_only_when_its_turn_comes(bucket):
    """S3 objects are opened one at a time while streaming, not up front."""
    manifest = _manifest(bucket, {"a.txt": b"hello", "b.txt": b"world"})

    stream = archive_stream(manifest)
    assert not bucket.read

    b"".join(stream)
    assert bucket.read == ["k0", "k1"]


def test_download_slots_are_capped(settings):
    """Beyond the limit, a download is refused until another one ends."""
    settings.ARCHIVE_MAX_CONCURRENT_DOWNLOADS = 2

    assert acquire_download_slot()
    assert acquire_download_slot()
    assert not acquire_download_slot()

    release_download_slot()
    assert acquire_download_slot()


def test_release_without_slot_gives_no_extra_slot(settings):
    """Releasing after the counter expired neither raises, which would break
    the response, nor lets one more download in."""
    settings.ARCHIVE_MAX_CONCURRENT_DOWNLOADS = 1

    release_download_slot()

    assert acquire_download_slot()
    assert not acquire_download_slot()
