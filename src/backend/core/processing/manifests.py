"""Manifest helpers for tracking source and destination file IDs during migration."""

import json
import os
from typing import Iterator

FILE_MANIFEST = "_file_manifest.json"
DRIVE_MANIFEST = "_drive_manifest.json"


def write_file_manifest(local_path: str, manifest: dict) -> None:
    """Write {rel_path → source_file_id} to _file_manifest.json in local_path."""
    with open(os.path.join(local_path, FILE_MANIFEST), "w", encoding="utf-8") as f:
        json.dump(manifest, f)


def read_file_manifest(local_path: str) -> dict:
    """Read _file_manifest.json and return {rel_path → source_file_id}."""
    with open(os.path.join(local_path, FILE_MANIFEST), encoding="utf-8") as f:
        return json.load(f)


def write_drive_manifest(local_path: str, manifest: dict) -> None:
    """Write {rel_path → drive_file_id} to _drive_manifest.json in local_path."""
    with open(os.path.join(local_path, DRIVE_MANIFEST), "w", encoding="utf-8") as f:
        json.dump(manifest, f)


def read_drive_manifest(local_path: str) -> dict:
    """Read _drive_manifest.json and return {rel_path → drive_file_id}."""
    with open(os.path.join(local_path, DRIVE_MANIFEST), encoding="utf-8") as f:
        return json.load(f)


def iter_file_id_map(local_path: str) -> Iterator[tuple[str, str]]:
    """Yield (source_file_id, drive_file_id) for each file present in both manifests."""
    file_manifest = read_file_manifest(local_path)
    drive_manifest = read_drive_manifest(local_path)
    for rel_path, source_id in file_manifest.items():
        drive_id = drive_manifest.get(rel_path)
        if drive_id:
            yield source_id, drive_id
