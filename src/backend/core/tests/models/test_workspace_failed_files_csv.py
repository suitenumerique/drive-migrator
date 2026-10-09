"""Tests for the CSV listing the files a migration could not migrate."""

import csv
import io

from core.destinations.drive.drive_backend import FileRejection
from core.models import DRIVE_REJECTION_REASONS, Workspace


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
    """A Drive refusal is listed with the label matching its Drive code."""
    workspace = Workspace(
        download_errors=[],
        upload_errors=[
            {
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
        "Envoi vers Drive",
        "Type de fichier non autorisé",
        "file_type_not_allowed",
    ]


def test_get_failed_files_csv_labels_every_drive_rejection_code():
    """Each known Drive rejection code gets its own readable reason."""
    codes = {
        "item_create_file_extension_not_allowed": "Extension de fichier non autorisée",
        "file_type_not_allowed": "Type de fichier non autorisé",
        "file_size_exceeded": "Fichier trop volumineux",
        "file_size_mismatch": "Fichier corrompu pendant le transfert",
        "blocked_upstream": "Fichier bloqué en amont de Drive (ex. pare-feu)",
    }
    workspace = Workspace(
        download_errors=[],
        upload_errors=[
            {"path": f"{code}.bin", "item_id": code, "error": code} for code in codes
        ],
    )

    rows = _rows(workspace.get_failed_files_csv("Osmose"))

    assert {row[4]: row[3] for row in rows[1:]} == codes


def test_every_file_rejection_has_a_reason():
    """A new FileRejection can't reach the CSV without its readable reason."""
    assert set(DRIVE_REJECTION_REASONS) == set(FileRejection)


def test_get_failed_files_csv_keeps_unknown_drive_code():
    """An unknown Drive code still gets a generic reason and keeps its code."""
    workspace = Workspace(
        download_errors=[],
        upload_errors=[{"path": "a.txt", "item_id": "i", "error": "new_code"}],
    )

    rows = _rows(workspace.get_failed_files_csv("Osmose"))

    assert rows[1][3:] == ["Fichier refusé par Drive", "new_code"]


def test_get_failed_files_csv_lists_download_errors_before_drive_rejections():
    """Download failures come first, in the order the run met them."""
    workspace = Workspace(
        download_errors=[
            {"id": "1", "name": "b.pdf", "path": "b.pdf", "error": "boom"},
            {"id": "2", "name": "a.pdf", "path": "a.pdf", "error": "boom"},
        ],
        upload_errors=[
            {"path": "c.exe", "item_id": "i", "error": "file_type_not_allowed"}
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
