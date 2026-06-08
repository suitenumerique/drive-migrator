"""Tests for ResanaPermissionReader."""

from unittest.mock import MagicMock, call

import pytest

from core.permissions.models import (
    CanonicalRole,
    NormalizedFilePermission,
    PermissionTarget,
    UserPermission,
)
from core.sources.resana.resana_permission_reader import (
    ResanaPermissionReader,
    _resolve_php_folder_paths,
)

PHP_SLUG = "2137419"
GED_WS_UUID = "02-01-aaaabbbb"


def _make_reader():
    php_client = MagicMock()
    ged_client = MagicMock()
    reader = ResanaPermissionReader(php_client, ged_client, PHP_SLUG, GED_WS_UUID)
    return reader, php_client, ged_client


# ---------------------------------------------------------------------------
# Group 1 — get_workspace_members
# ---------------------------------------------------------------------------


def test_get_workspace_members_returns_user_permissions():
    reader, php_client, _ = _make_reader()
    php_client.list_workspace_members.return_value = [
        {"id": "1", "email": "admin@example.com", "profil_droit": "10821219"},
        {"id": "2", "email": "user@example.com", "profil_droit": "10821218"},
        {"id": "3", "email": "guest@example.com", "profil_droit": "10821217"},
    ]

    result = reader.get_workspace_members(PHP_SLUG)

    php_client.list_workspace_members.assert_called_once_with(PHP_SLUG)
    assert len(result) == 3
    assert result[0] == UserPermission(
        email="admin@example.com", role=CanonicalRole.MANAGE
    )
    assert result[1] == UserPermission(
        email="user@example.com", role=CanonicalRole.WRITE
    )
    assert result[2] == UserPermission(
        email="guest@example.com", role=CanonicalRole.READ
    )


def test_get_workspace_members_unknown_role_falls_back_to_read():
    reader, php_client, _ = _make_reader()
    php_client.list_workspace_members.return_value = [
        {"id": "1", "email": "x@example.com", "profil_droit": "99999"},
    ]

    result = reader.get_workspace_members(PHP_SLUG)

    assert result[0].role == CanonicalRole.READ


# ---------------------------------------------------------------------------
# Group 2 — get_file_permission (via injected cache)
# ---------------------------------------------------------------------------


def test_get_file_permission_returns_none_for_unknown_uuid():
    reader, _, _ = _make_reader()
    reader._permission_cache = {}

    assert reader.get_file_permission("unknown-uuid") is None


def test_get_file_permission_returns_cached_permission():
    reader, _, _ = _make_reader()
    perm = NormalizedFilePermission(target=PermissionTarget.ALL_MEMBERS)
    reader._permission_cache = {"ged-uuid-1": perm}

    assert reader.get_file_permission("ged-uuid-1") is perm


def test_get_file_permission_triggers_cache_build_when_none():
    reader, php_client, ged_client = _make_reader()
    php_client.get_folders.return_value = []
    ged_client.explore.return_value = []

    result = reader.get_file_permission("unknown")

    assert result is None
    php_client.get_folders.assert_called_once()


def test_get_file_permission_cache_built_only_once():
    reader, php_client, ged_client = _make_reader()
    reader._permission_cache = {}  # pre-inject empty cache

    reader.get_file_permission("x")
    reader.get_file_permission("y")

    php_client.get_folders.assert_not_called()


# ---------------------------------------------------------------------------
# Group 3 — _resolve_permission
# ---------------------------------------------------------------------------


def test_resolve_permission_unlocked_returns_all_members():
    reader, _, _ = _make_reader()
    result = reader._resolve_permission(php_id=100, sharing_type="UNLOCKED")
    assert result == NormalizedFilePermission(target=PermissionTarget.ALL_MEMBERS)


def test_resolve_permission_locked_returns_private():
    reader, _, _ = _make_reader()
    result = reader._resolve_permission(php_id=100, sharing_type="LOCKED")
    assert result == NormalizedFilePermission(target=PermissionTarget.PRIVATE)


def test_resolve_permission_restricted_calls_get_file_details():
    reader, php_client, _ = _make_reader()
    php_client.get_file_details.return_value = {
        "information": {"visible_profil_droit": "GESTIONNAIRE_CONTRIBUTEUR"},
        "tab_information_restreints": {},
        "tab_profil_droit_sources": {},
    }

    result = reader._resolve_permission(php_id=100, sharing_type="RESTRICTED")

    php_client.get_file_details.assert_called_once_with(PHP_SLUG, 100)
    assert result.target == PermissionTarget.MANAGERS_CONTRIBUTORS


# ---------------------------------------------------------------------------
# Group 4 — _resolve_restricted
# ---------------------------------------------------------------------------


def test_resolve_restricted_managers_contributors():
    reader, _, _ = _make_reader()
    details = {
        "information": {"visible_profil_droit": "GESTIONNAIRE_CONTRIBUTEUR"},
        "tab_information_restreints": {},
        "tab_profil_droit_sources": {},
    }
    result = reader._resolve_restricted(php_id=100, details=details)
    assert result == NormalizedFilePermission(
        target=PermissionTarget.MANAGERS_CONTRIBUTORS
    )


def test_resolve_restricted_groups():
    reader, _, _ = _make_reader()
    details = {
        "information": {"visible_profil_droit": None},
        "tab_information_restreints": {"10174": {"id": "x"}, "10176": {"id": "y"}},
        "tab_profil_droit_sources": {},
    }
    result = reader._resolve_restricted(php_id=100, details=details)
    assert result.target == PermissionTarget.RESTRICTED_GROUPS
    assert set(result.groups) == {"AGENT", "PARTENAIRE"}


def test_resolve_restricted_groups_unknown_id_ignored():
    reader, _, _ = _make_reader()
    details = {
        "information": {"visible_profil_droit": None},
        "tab_information_restreints": {"10174": {}, "99999": {}},
        "tab_profil_droit_sources": {},
    }
    result = reader._resolve_restricted(php_id=100, details=details)
    assert result.groups == ["AGENT"]


def test_resolve_restricted_specific_users():
    reader, php_client, _ = _make_reader()
    php_client.list_users_by_file.return_value = [
        {
            "mail_inscription": "a@example.com",
            "profilDroitSourceObjet": {"objet_source": "377429805"},
        },
        {
            "mail_inscription": "b@example.com",
            "profilDroitSourceObjet": {"objet_source": "377429806"},
        },
    ]
    details = {
        "information": {"visible_profil_droit": None},
        "tab_information_restreints": {},
        "tab_profil_droit_sources": {
            "377429805": {"profil_droit": "10821219"},  # MANAGE
            "377429806": {"profil_droit": "10821217"},  # READ
        },
    }

    result = reader._resolve_restricted(php_id=100, details=details)

    assert result.target == PermissionTarget.SPECIFIC_USERS
    assert len(result.user_permissions) == 2
    emails = {up.email for up in result.user_permissions}
    assert emails == {"a@example.com", "b@example.com"}
    roles_by_email = {up.email: up.role for up in result.user_permissions}
    assert roles_by_email["a@example.com"] == CanonicalRole.MANAGE
    assert roles_by_email["b@example.com"] == CanonicalRole.READ


def test_resolve_restricted_specific_users_skips_unmatched():
    reader, php_client, _ = _make_reader()
    php_client.list_users_by_file.return_value = [
        {
            "mail_inscription": "a@example.com",
            "profilDroitSourceObjet": {"objet_source": "NOT_IN_TAB"},
        },
    ]
    details = {
        "information": {"visible_profil_droit": None},
        "tab_information_restreints": {},
        "tab_profil_droit_sources": {},
    }

    result = reader._resolve_restricted(php_id=100, details=details)

    assert not result.user_permissions


# ---------------------------------------------------------------------------
# Group 5 — _translate_role (static)
# ---------------------------------------------------------------------------


def test_translate_role_gestionnaire():
    assert ResanaPermissionReader._translate_role("10821219") == CanonicalRole.MANAGE


def test_translate_role_contributeur():
    assert ResanaPermissionReader._translate_role("10821218") == CanonicalRole.WRITE


def test_translate_role_visiteur():
    assert ResanaPermissionReader._translate_role("10821217") == CanonicalRole.READ


def test_translate_role_unknown_falls_back_to_read():
    assert ResanaPermissionReader._translate_role("99999") == CanonicalRole.READ


# ---------------------------------------------------------------------------
# Group 6 — _resolve_php_folder_paths (module-level helper)
# ---------------------------------------------------------------------------


def test_resolve_php_folder_paths_root_folders():
    folders = [
        {"id": 100, "name": "DossierA", "dossier_mere": None},
        {"id": 101, "name": "DossierB", "dossier_mere": None},
    ]
    result = _resolve_php_folder_paths(folders)
    assert result == {100: "DossierA", 101: "DossierB"}


def test_resolve_php_folder_paths_nested():
    folders = [
        {"id": 100, "name": "Parent", "dossier_mere": None},
        {"id": 101, "name": "Child", "dossier_mere": 100},
        {"id": 102, "name": "GrandChild", "dossier_mere": 101},
    ]
    result = _resolve_php_folder_paths(folders)
    assert result[100] == "Parent"
    assert result[101] == "Parent/Child"
    assert result[102] == "Parent/Child/GrandChild"


# ---------------------------------------------------------------------------
# Group 7 — _build_ged_file_map
# ---------------------------------------------------------------------------


def _ged_explore(members):
    """Helper: wrap explore response the way InterstisClient returns it."""
    return [
        {
            "name": "",
            "folders": members.get("folders", []),
            "files": members.get("files", []),
        }
    ]


def test_build_ged_file_map_single_folder():
    reader, _, ged_client = _make_reader()
    ged_client.explore.side_effect = [
        # workspace root → one sub-folder, no root files
        _ged_explore(
            {"folders": [{"uuid": "folder-uuid", "name": "DossierA"}], "files": []}
        ),
        # DossierA → two files
        _ged_explore(
            {
                "folders": [],
                "files": [
                    {"uuid": "file-1-uuid", "name": "rapport"},
                    {"uuid": "file-2-uuid", "name": "notes"},
                ],
            }
        ),
    ]

    result = reader._build_ged_file_map("", GED_WS_UUID)

    assert result == {
        "DossierA": {"rapport": "file-1-uuid", "notes": "file-2-uuid"},
    }


def test_build_ged_file_map_nested_folders():
    reader, _, ged_client = _make_reader()
    ged_client.explore.side_effect = [
        _ged_explore(
            {"folders": [{"uuid": "parent-uuid", "name": "Parent"}], "files": []}
        ),
        _ged_explore(
            {"folders": [{"uuid": "child-uuid", "name": "Child"}], "files": []}
        ),
        _ged_explore({"folders": [], "files": [{"uuid": "f-uuid", "name": "doc"}]}),
    ]

    result = reader._build_ged_file_map("", GED_WS_UUID)

    assert "Parent/Child" in result
    assert result["Parent/Child"]["doc"] == "f-uuid"


def test_build_ged_file_map_empty_workspace():
    reader, _, ged_client = _make_reader()
    ged_client.explore.return_value = []

    result = reader._build_ged_file_map("", GED_WS_UUID)

    assert result == {}


# ---------------------------------------------------------------------------
# Group 8 — _build_permission_cache (end-to-end)
# ---------------------------------------------------------------------------


def test_build_permission_cache_maps_ged_uuids_to_permissions():
    reader, php_client, ged_client = _make_reader()

    php_client.get_folders.return_value = [
        {"id": 100, "name": "DossierA", "dossier_mere": None},
    ]
    ged_client.explore.side_effect = [
        _ged_explore(
            {"folders": [{"uuid": "folder-ged-uuid", "name": "DossierA"}], "files": []}
        ),
        _ged_explore(
            {"folders": [], "files": [{"uuid": "file-ged-uuid", "name": "rapport"}]}
        ),
    ]
    php_client.get_ged_info.return_value = [
        {"id": 200, "titre": "rapport.pdf", "sharingType": "UNLOCKED"},
    ]

    cache = reader._build_permission_cache()

    assert "file-ged-uuid" in cache
    assert cache["file-ged-uuid"].target == PermissionTarget.ALL_MEMBERS


def test_build_permission_cache_skips_folder_with_no_ged_counterpart():
    reader, php_client, ged_client = _make_reader()

    php_client.get_folders.return_value = [
        {"id": 100, "name": "OnlyInPHP", "dossier_mere": None},
    ]
    ged_client.explore.side_effect = [
        _ged_explore(
            {"folders": [{"uuid": "f-uuid", "name": "OnlyInGED"}], "files": []}
        ),
        _ged_explore({"folders": [], "files": [{"uuid": "x-uuid", "name": "doc"}]}),
    ]

    cache = reader._build_permission_cache()

    php_client.get_ged_info.assert_not_called()
    assert not cache


def test_build_permission_cache_skips_unmatched_php_files():
    reader, php_client, ged_client = _make_reader()

    php_client.get_folders.return_value = [
        {"id": 100, "name": "DossierA", "dossier_mere": None},
    ]
    ged_client.explore.side_effect = [
        _ged_explore(
            {"folders": [{"uuid": "folder-ged-uuid", "name": "DossierA"}], "files": []}
        ),
        _ged_explore(
            {"folders": [], "files": [{"uuid": "file-ged-uuid", "name": "doc"}]}
        ),
    ]
    php_client.get_ged_info.return_value = [
        {"id": 200, "titre": "other_name.pdf", "sharingType": "UNLOCKED"},
    ]

    cache = reader._build_permission_cache()

    assert not cache


def test_build_permission_cache_strips_extension_for_matching():
    reader, php_client, ged_client = _make_reader()

    php_client.get_folders.return_value = [
        {"id": 100, "name": "Docs", "dossier_mere": None},
    ]
    ged_client.explore.side_effect = [
        _ged_explore({"folders": [{"uuid": "f-uuid", "name": "Docs"}], "files": []}),
        _ged_explore(
            {"folders": [], "files": [{"uuid": "my-file-uuid", "name": "my_file"}]}
        ),
    ]
    php_client.get_ged_info.return_value = [
        {"id": 300, "titre": "my_file.docx", "sharingType": "LOCKED"},
    ]

    cache = reader._build_permission_cache()

    assert cache["my-file-uuid"].target == PermissionTarget.PRIVATE


def test_build_permission_cache_matches_file_without_extension():
    reader, php_client, ged_client = _make_reader()

    php_client.get_folders.return_value = [
        {"id": 100, "name": "Docs", "dossier_mere": None},
    ]
    ged_client.explore.side_effect = [
        _ged_explore({"folders": [{"uuid": "f-uuid", "name": "Docs"}], "files": []}),
        _ged_explore(
            {"folders": [], "files": [{"uuid": "readme-uuid", "name": "README"}]}
        ),
    ]
    php_client.get_ged_info.return_value = [
        {"id": 400, "titre": "README", "sharingType": "UNLOCKED"},
    ]

    cache = reader._build_permission_cache()

    assert "readme-uuid" in cache
    assert cache["readme-uuid"].target == PermissionTarget.ALL_MEMBERS


def test_build_permission_cache_compound_extension_matches_ged_name():
    # "archive.tar.gz" → base name "archive.tar", which must match GED name "archive.tar"
    reader, php_client, ged_client = _make_reader()

    php_client.get_folders.return_value = [
        {"id": 100, "name": "Docs", "dossier_mere": None},
    ]
    ged_client.explore.side_effect = [
        _ged_explore({"folders": [{"uuid": "f-uuid", "name": "Docs"}], "files": []}),
        _ged_explore(
            {"folders": [], "files": [{"uuid": "arc-uuid", "name": "archive.tar"}]}
        ),
    ]
    php_client.get_ged_info.return_value = [
        {"id": 500, "titre": "archive.tar.gz", "sharingType": "UNLOCKED"},
    ]

    cache = reader._build_permission_cache()

    assert "arc-uuid" in cache
