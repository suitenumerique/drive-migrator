"""Tests for the CSV listing the files a migration could not migrate."""

import csv
import io

import pytest

from core.backends.destination import AbstractDestinationBackend, DestinationRegistry
from core.models import Workspace


class RefusingDestination(AbstractDestinationBackend):
    """Destination without its own rejection reasons."""

    name = "refusing"
    label = "Refusing Destination"

    def export(self, workspace, user, local_folder_path):
        pass


@pytest.fixture(autouse=True)
def _destinations(settings):
    """Register Drive and a destination keeping the default rejection reason."""
    settings.DESTINATION_BACKENDS = [
        "core.destinations.drive.backend.DriveDestinationBackend",
        f"{__name__}.RefusingDestination",
    ]
    DestinationRegistry.clear_cache()
    yield
    DestinationRegistry.clear_cache()


def _rows(content: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(content.removeprefix("﻿")), delimiter=";"))


def test_get_failed_files_csv_returns_none_without_errors():
    """No CSV is built when no file failed."""
    workspace = Workspace(download_errors=[], upload_errors=[])

    assert workspace.get_failed_files_csv("Osmose") is None


def test_get_failed_files_csv_starts_with_bom_for_excel():
    """The CSV starts with a UTF-8 BOM so Excel detects the encoding."""
    workspace = Workspace(
        download_errors=[
            {"id": "1", "name": "a.pdf", "path": "a.pdf", "error": "boom"}
        ],
        upload_errors=[],
    )

    assert workspace.get_failed_files_csv("Osmose").startswith("﻿")


def test_get_failed_files_csv_lists_download_errors():
    """A download failure is listed with a readable reason, not the raw exception."""
    workspace = Workspace(
        download_errors=[
            {
                "id": "src-1",
                "name": "compte-rendu.docx",
                "path": "Projets/2025/compte-rendu.docx",
                "error": "403 Client Error: Forbidden for url: https://internal/x",
            }
        ],
        upload_errors=[],
    )

    rows = _rows(workspace.get_failed_files_csv("Osmose"))

    assert rows == [
        ["Chemin", "Nom du fichier", "Étape", "Motif", "Code"],
        [
            "Projets/2025/compte-rendu.docx",
            "compte-rendu.docx",
            "Récupération depuis Osmose",
            "Le fichier n'a pas pu être récupéré depuis Osmose",
            "download_failed",
        ],
    ]


def test_get_failed_files_csv_lists_drive_rejections():
    """A Drive refusal is listed with the reason Drive gives for its code."""
    workspace = Workspace(
        download_errors=[],
        upload_errors=[
            {
                "destination": "drive",
                "path": "Divers/setup.exe",
                "item_id": "item-1",
                "error": "file_type_not_allowed",
            }
        ],
    )

    rows = _rows(workspace.get_failed_files_csv("Osmose"))

    assert rows[1] == [
        "Divers/setup.exe",
        "setup.exe",
        "Envoi vers La Suite Drive",
        "Type de fichier non autorisé",
        "file_type_not_allowed",
    ]


def test_get_failed_files_csv_uses_the_reason_of_the_refusing_destination():
    """Any destination names the step and explains its own codes."""
    workspace = Workspace(
        download_errors=[],
        upload_errors=[
            {"destination": "refusing", "path": "a/b.txt", "error": "some_code"}
        ],
    )

    rows = _rows(workspace.get_failed_files_csv("Osmose"))

    assert rows[1] == [
        "a/b.txt",
        "b.txt",
        "Envoi vers Refusing Destination",
        "Fichier refusé par Refusing Destination",
        "some_code",
    ]


def test_get_failed_files_csv_names_an_unregistered_destination():
    """An entry of a destination no longer configured still gets a row, so the
    CSV, and the mail it is attached to, never fail on it."""
    workspace = Workspace(
        download_errors=[],
        upload_errors=[{"destination": "gone", "path": "a.txt", "error": "nope"}],
    )

    rows = _rows(workspace.get_failed_files_csv("Osmose"))

    assert rows[1] == [
        "a.txt",
        "a.txt",
        "Envoi vers gone",
        "Fichier refusé par gone",
        "nope",
    ]


def test_get_failed_files_csv_attributes_legacy_entries_to_drive():
    """Entries recorded before the destination was stored came from Drive."""
    workspace = Workspace(
        download_errors=[],
        upload_errors=[
            {"path": "a.exe", "item_id": "i", "error": "file_type_not_allowed"}
        ],
    )

    rows = _rows(workspace.get_failed_files_csv("Osmose"))

    assert rows[1][2:4] == ["Envoi vers La Suite Drive", "Type de fichier non autorisé"]


def test_get_failed_files_csv_lists_download_errors_before_rejections():
    """Download failures come first, in the order the run met them."""
    workspace = Workspace(
        download_errors=[
            {"id": "1", "name": "b.pdf", "path": "b.pdf", "error": "boom"},
            {"id": "2", "name": "a.pdf", "path": "a.pdf", "error": "boom"},
        ],
        upload_errors=[
            {
                "destination": "drive",
                "path": "c.exe",
                "item_id": "i",
                "error": "file_type_not_allowed",
            }
        ],
    )

    rows = _rows(workspace.get_failed_files_csv("Osmose"))

    assert [row[0] for row in rows[1:]] == ["b.pdf", "a.pdf", "c.exe"]


def test_get_failed_files_csv_uses_source_label():
    """The download step names the source the workspace was migrated from."""
    workspace = Workspace(
        download_errors=[{"id": "1", "name": "a.pdf", "path": "a.pdf", "error": "x"}],
        upload_errors=[],
    )

    rows = _rows(workspace.get_failed_files_csv("Système de fichiers"))

    assert rows[1][2:4] == [
        "Récupération depuis Système de fichiers",
        "Le fichier n'a pas pu être récupéré depuis Système de fichiers",
    ]
