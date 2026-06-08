"""Tests for ResanaPhpClient — Resana internal PHP API client."""

from unittest.mock import MagicMock, patch

import pytest
import requests as req_lib

from core.sources.resana.resana_php_client import ResanaPhpClient

BASE_URL = "https://preprod.resana.numerique.gouv.fr"
SLUG = "2137419"

_SAMPLE_HTML = """
<script>
var information = JSON.parse('{"id":"48005540","visible_all_membre":"0","visible_profil_droit":null}');
var tabProfilDroitSources = JSON.parse('{"377429805":{"profil_droit":"10821219"}}');
var tabInformationRestreints = JSON.parse('{}');
var tabProfilDroits = JSON.parse('[{"id":"10821217","code":"VISITEUR"}]');
</script>
"""

_HTML_ESCAPED_QUOTES = r"""
<script>
var information = JSON.parse('{"id":"1","titre":"it\'s a file"}');
var tabProfilDroitSources = JSON.parse('{}');
var tabInformationRestreints = JSON.parse('{}');
var tabProfilDroits = JSON.parse('[]');
</script>
"""

_HTML_MISSING_VARS = "<html>no js vars here</html>"


def _make_client(token="test-token"):
    client = ResanaPhpClient(token, BASE_URL)
    client.session = MagicMock()
    return client


# ---------------------------------------------------------------------------
# Group 1 — Constructor
# ---------------------------------------------------------------------------


def test_init_sets_access_token_cookie():
    with patch("core.sources.resana.resana_php_client.requests.Session") as MockSession:
        ResanaPhpClient("my-token", BASE_URL)

    MockSession.return_value.cookies.set.assert_called_once_with(
        "interstis_access", "my-token"
    )


def test_init_sets_xhr_header():
    with patch("core.sources.resana.resana_php_client.requests.Session") as MockSession:
        ResanaPhpClient("my-token", BASE_URL)

    MockSession.return_value.headers.__setitem__.assert_called_with(
        "X-Requested-With", "XMLHttpRequest"
    )


def test_init_csrf_token_is_none():
    client = ResanaPhpClient("tok", BASE_URL)
    assert client._csrf_token is None


# ---------------------------------------------------------------------------
# Group 2 — _ensure_csrf()
# ---------------------------------------------------------------------------


def test_ensure_csrf_fetches_token_from_cookie():
    client = _make_client()
    client.session.get.return_value.cookies = {"CSRF-TOKEN": "deadbeef"}

    client._ensure_csrf(SLUG)

    client.session.get.assert_called_once_with(
        f"{BASE_URL}/public/perimetre/consulter/{SLUG}", timeout=30
    )
    assert client._csrf_token == "deadbeef"
    client.session.headers.__setitem__.assert_called_with("X-CSRF-Token", "deadbeef")


def test_ensure_csrf_raises_on_http_error():
    client = _make_client()
    client.session.get.return_value.raise_for_status.side_effect = req_lib.HTTPError(
        "401"
    )

    with pytest.raises(req_lib.HTTPError):
        client._ensure_csrf(SLUG)


def test_ensure_csrf_not_called_twice():
    client = _make_client()
    client._csrf_token = "cached"

    client._ensure_csrf(SLUG)

    client.session.get.assert_not_called()


# ---------------------------------------------------------------------------
# Group 3 — get_folders()
# ---------------------------------------------------------------------------


def test_get_folders_returns_folder_list():
    client = _make_client()
    client._csrf_token = "tok"
    client.session.post.return_value.json.return_value = {
        "folders": [
            {"id": 100, "name": "DossierA", "dossier_mere": None},
            {"id": 101, "name": "DossierB", "dossier_mere": None},
        ]
    }
    client.session.post.return_value.raise_for_status = MagicMock()

    result = client.get_folders(SLUG)

    client.session.post.assert_called_once_with(
        f"{BASE_URL}/public/dossier/getFolders",
        params={"slug": SLUG},
        data={"allFolders": "true", "perimeterId": SLUG},
        timeout=30,
    )
    assert len(result) == 2
    assert result[0]["name"] == "DossierA"


def test_get_folders_returns_empty_list():
    client = _make_client()
    client._csrf_token = "tok"
    client.session.post.return_value.json.return_value = {"folders": []}
    client.session.post.return_value.raise_for_status = MagicMock()

    assert not client.get_folders(SLUG)


# ---------------------------------------------------------------------------
# Group 4 — get_ged_info()
# ---------------------------------------------------------------------------


def test_get_ged_info_returns_files_single_page():
    client = _make_client()
    client._csrf_token = "tok"
    client.session.post.return_value.json.return_value = {
        "gedTab": [
            {"id": 10, "titre": "doc.pdf", "sharingType": "UNLOCKED"},
            {"id": 11, "titre": "img.jpg", "sharingType": "LOCKED"},
        ],
        "nb_info": "2",
    }
    client.session.post.return_value.raise_for_status = MagicMock()

    result = client.get_ged_info(SLUG, folder_id=100)

    assert len(result) == 2
    assert result[0]["sharingType"] == "UNLOCKED"


def test_get_ged_info_paginates_across_multiple_pages():
    client = _make_client()
    client._csrf_token = "tok"
    page1 = {"gedTab": [{"id": i} for i in range(100)], "nb_info": "150"}
    page2 = {"gedTab": [{"id": i} for i in range(100, 150)], "nb_info": "150"}
    client.session.post.return_value.raise_for_status = MagicMock()
    client.session.post.return_value.json.side_effect = [page1, page2]

    result = client.get_ged_info(SLUG, folder_id=100, limit=100)

    assert client.session.post.call_count == 2
    assert len(result) == 150


def test_get_ged_info_returns_empty_list_for_empty_folder():
    client = _make_client()
    client._csrf_token = "tok"
    client.session.post.return_value.json.return_value = {
        "gedTab": [],
        "nb_info": "0",
    }
    client.session.post.return_value.raise_for_status = MagicMock()

    result = client.get_ged_info(SLUG, folder_id=100)

    client.session.post.assert_called_once()
    assert not result


def test_get_ged_info_raises_on_http_error():
    client = _make_client()
    client._csrf_token = "tok"
    client.session.post.return_value.raise_for_status.side_effect = req_lib.HTTPError(
        "503"
    )

    with pytest.raises(req_lib.HTTPError):
        client.get_ged_info(SLUG, folder_id=100)


# ---------------------------------------------------------------------------
# Group 5 — _parse_file_details_html() (static, no HTTP)
# ---------------------------------------------------------------------------


def test_parse_file_details_html_extracts_all_vars():
    result = ResanaPhpClient._parse_file_details_html(_SAMPLE_HTML)

    assert result["information"]["id"] == "48005540"
    assert result["information"]["visible_profil_droit"] is None
    assert "377429805" in result["tab_profil_droit_sources"]
    assert not result["tab_information_restreints"]
    assert result["tab_profil_droits"][0]["code"] == "VISITEUR"


def test_parse_file_details_html_handles_escaped_quotes():
    result = ResanaPhpClient._parse_file_details_html(_HTML_ESCAPED_QUOTES)

    assert result["information"]["titre"] == "it's a file"


def test_parse_file_details_html_uses_defaults_when_vars_missing():
    result = ResanaPhpClient._parse_file_details_html(_HTML_MISSING_VARS)

    assert result["information"] == {}
    assert result["tab_profil_droit_sources"] == {}
    assert result["tab_information_restreints"] == {}
    assert result["tab_profil_droits"] == []


# ---------------------------------------------------------------------------
# Group 6 — get_file_details()
# ---------------------------------------------------------------------------


def test_get_file_details_calls_correct_endpoint():
    client = _make_client()
    client._csrf_token = "tok"
    client.session.post.return_value.text = _SAMPLE_HTML
    client.session.post.return_value.raise_for_status = MagicMock()

    client.get_file_details(SLUG, php_file_id=48005540)

    client.session.post.assert_called_once_with(
        f"{BASE_URL}/public/information/afficherProfilDroitInformation",
        params={"startAide": "0", "peri": SLUG},
        data={
            "juste_retour": "true",
            "horsPerimetre": "false",
            "perimetre_selectionne": SLUG,
            "tab_id_infos": "[48005540]",
            "fonction_retour": "sendToVue",
        },
        timeout=30,
    )


def test_get_file_details_returns_parsed_result():
    client = _make_client()
    client._csrf_token = "tok"
    client.session.post.return_value.text = _SAMPLE_HTML
    client.session.post.return_value.raise_for_status = MagicMock()

    result = client.get_file_details(SLUG, php_file_id=48005540)

    assert result["information"]["id"] == "48005540"


# ---------------------------------------------------------------------------
# Group 7 — list_users_by_file()
# ---------------------------------------------------------------------------


def test_list_users_by_file_returns_users():
    client = _make_client()
    client._csrf_token = "tok"
    client.session.post.return_value.json.return_value = [
        {
            "id": "1",
            "mail_inscription": "a@example.com",
            "profilDroitSourceObjet": {"profil_droit": "10821219"},
        },
        {
            "id": "2",
            "mail_inscription": "b@example.com",
            "profilDroitSourceObjet": {"profil_droit": "10821218"},
        },
    ]
    client.session.post.return_value.raise_for_status = MagicMock()

    result = client.list_users_by_file(SLUG, php_file_id=48005543)

    client.session.post.assert_called_once_with(
        f"{BASE_URL}/public/utilisateur/listerUtilisateurByPerimetreAndGroupe",
        data={
            "uniquementSelected": "true",
            "id_information": 48005543,
            "id_perimetre": SLUG,
            "offcetChargerUtilisateurs": 0,
            "nom_prenom": "",
            "dossier_id": "",
        },
        timeout=30,
    )
    assert len(result) == 2
    assert result[0]["mail_inscription"] == "a@example.com"


def test_list_users_by_file_paginates_when_full_page():
    client = _make_client()
    client._csrf_token = "tok"
    page1 = [{"id": str(i), "mail_inscription": f"u{i}@x.com"} for i in range(50)]
    page2 = [{"id": "50", "mail_inscription": "u50@x.com"}]
    client.session.post.return_value.raise_for_status = MagicMock()
    client.session.post.return_value.json.side_effect = [page1, page2]

    result = client.list_users_by_file(SLUG, php_file_id=48005543)

    assert client.session.post.call_count == 2
    assert len(result) == 51


def test_list_users_by_file_stops_on_empty_response():
    client = _make_client()
    client._csrf_token = "tok"
    client.session.post.return_value.json.return_value = []
    client.session.post.return_value.raise_for_status = MagicMock()

    result = client.list_users_by_file(SLUG, php_file_id=48005543)

    assert not result
    client.session.post.assert_called_once()


# ---------------------------------------------------------------------------
# Group 8 — list_workspace_members()
# ---------------------------------------------------------------------------


def test_list_workspace_members_returns_members():
    client = _make_client()
    client._csrf_token = "tok"
    client.session.post.return_value.json.return_value = [
        {"id": "1", "email": "admin@example.com", "profil_droit": "10821219"},
        {"id": "2", "email": "user@example.com", "profil_droit": "10821218"},
    ]
    client.session.post.return_value.raise_for_status = MagicMock()

    result = client.list_workspace_members(SLUG)

    client.session.post.assert_called_once_with(
        f"{BASE_URL}/public/perimetre/listerUtilisateursAdminDroits",
        data={"perimetre_id": SLUG},
        timeout=30,
    )
    assert len(result) == 2
    assert result[0]["email"] == "admin@example.com"


def test_list_workspace_members_raises_on_http_error():
    client = _make_client()
    client._csrf_token = "tok"
    client.session.post.return_value.raise_for_status.side_effect = req_lib.HTTPError(
        "401"
    )

    with pytest.raises(req_lib.HTTPError):
        client.list_workspace_members(SLUG)
