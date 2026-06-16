"""Tests for core/processing/manifests.py."""

import json
import os

import pytest

from core.processing.manifests import (
    DRIVE_MANIFEST,
    FILE_MANIFEST,
    iter_file_id_map,
    read_drive_manifest,
    read_file_manifest,
    write_drive_manifest,
    write_file_manifest,
)

# ---------------------------------------------------------------------------
# Group 1 — write_file_manifest / read_file_manifest
# ---------------------------------------------------------------------------


def test_write_file_manifest_creates_json_file(tmp_path):
    """write_file_manifest() creates _file_manifest.json in the given directory."""
    write_file_manifest(str(tmp_path), {"subdir/doc.pdf": "uuid-1"})

    assert (tmp_path / FILE_MANIFEST).exists()


def test_write_file_manifest_content(tmp_path):
    """write_file_manifest() serialises the mapping to JSON without data loss."""
    manifest = {"doc.pdf": "uuid-1", "subdir/img.png": "uuid-2"}
    write_file_manifest(str(tmp_path), manifest)

    content = json.loads((tmp_path / FILE_MANIFEST).read_text())
    assert content == manifest


def test_read_file_manifest_returns_dict(tmp_path):
    """read_file_manifest() deserialises _file_manifest.json into a dict."""
    manifest = {"report.pdf": "src-uuid"}
    write_file_manifest(str(tmp_path), manifest)

    result = read_file_manifest(str(tmp_path))

    assert result == manifest


def test_file_manifest_round_trip(tmp_path):
    """Writing then reading _file_manifest.json returns the original mapping."""
    manifest = {"a/b/c.docx": "id-abc", "root.txt": "id-root"}
    write_file_manifest(str(tmp_path), manifest)

    assert read_file_manifest(str(tmp_path)) == manifest


# ---------------------------------------------------------------------------
# Group 2 — write_drive_manifest / read_drive_manifest
# ---------------------------------------------------------------------------


def test_write_drive_manifest_creates_json_file(tmp_path):
    """write_drive_manifest() creates _drive_manifest.json in the given directory."""
    write_drive_manifest(str(tmp_path), {"doc.pdf": "drive-id-1"})

    assert (tmp_path / DRIVE_MANIFEST).exists()


def test_write_drive_manifest_content(tmp_path):
    """write_drive_manifest() serialises the mapping to JSON without data loss."""
    manifest = {"doc.pdf": "drive-1", "sub/img.png": "drive-2"}
    write_drive_manifest(str(tmp_path), manifest)

    content = json.loads((tmp_path / DRIVE_MANIFEST).read_text())
    assert content == manifest


def test_read_drive_manifest_returns_dict(tmp_path):
    """read_drive_manifest() deserialises _drive_manifest.json into a dict."""
    manifest = {"report.pdf": "drive-uuid"}
    write_drive_manifest(str(tmp_path), manifest)

    assert read_drive_manifest(str(tmp_path)) == manifest


def test_drive_manifest_round_trip(tmp_path):
    """Writing then reading _drive_manifest.json returns the original mapping."""
    manifest = {"a/b/c.docx": "drive-abc", "root.txt": "drive-root"}
    write_drive_manifest(str(tmp_path), manifest)

    assert read_drive_manifest(str(tmp_path)) == manifest


# ---------------------------------------------------------------------------
# Group 3 — iter_file_id_map
# ---------------------------------------------------------------------------


def test_iter_file_id_map_yields_matched_pairs(tmp_path):
    """iter_file_id_map() yields (source_id, drive_id) for each path present in both manifests."""
    write_file_manifest(
        str(tmp_path),
        {
            "doc.pdf": "src-1",
            "img.png": "src-2",
        },
    )
    write_drive_manifest(
        str(tmp_path),
        {
            "doc.pdf": "drive-1",
            "img.png": "drive-2",
        },
    )

    result = list(iter_file_id_map(str(tmp_path)))

    assert ("src-1", "drive-1") in result
    assert ("src-2", "drive-2") in result


def test_iter_file_id_map_skips_unmatched_source_files(tmp_path):
    """iter_file_id_map() silently skips source files absent from the drive manifest."""
    write_file_manifest(
        str(tmp_path),
        {
            "doc.pdf": "src-1",
            "orphan.txt": "src-orphan",
        },
    )
    write_drive_manifest(
        str(tmp_path),
        {
            "doc.pdf": "drive-1",
        },
    )

    result = list(iter_file_id_map(str(tmp_path)))

    source_ids = [pair[0] for pair in result]
    assert "src-orphan" not in source_ids
    assert len(result) == 1


def test_iter_file_id_map_empty_when_no_overlap(tmp_path):
    """iter_file_id_map() yields nothing when file and drive manifests share no paths."""
    write_file_manifest(str(tmp_path), {"a.pdf": "src-1"})
    write_drive_manifest(str(tmp_path), {"b.pdf": "drive-1"})

    assert not list(iter_file_id_map(str(tmp_path)))


def test_iter_file_id_map_handles_nested_paths(tmp_path):
    """iter_file_id_map() matches on the full relative path including subdirectories."""
    write_file_manifest(str(tmp_path), {"DossierA/rapport.pdf": "src-nested"})
    write_drive_manifest(str(tmp_path), {"DossierA/rapport.pdf": "drive-nested"})

    result = list(iter_file_id_map(str(tmp_path)))

    assert result == [("src-nested", "drive-nested")]
