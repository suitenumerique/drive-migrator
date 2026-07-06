"""Tests for ResanaMembersClient — reads workspace members from the Resana PHP portal."""

# pylint: disable=protected-access
import base64
import json
from unittest.mock import MagicMock, patch

import pytest

from core.sources.resana.resana_members_client import (
    MissingPhpSessionId,
    ResanaMembersClient,
)

BASE_URL = "https://resana-web.example.test"
SLUG = "2137419"
PHP_SESSION_ID = "fdbcdafa71a19f7ef05c7562aef9cd29"

_CSRF_HTML = "<script>var CSRFToken = 'abc123def';</script>"

_ONGLET_TRIE_RESPONSE = {
    "tabData": [
        {
            "id": -1,
            "tabPerimetres": [
                {"id": "2137428", "nom": "Coucou"},
                {"id": "2137419", "nom": "TEST Worskspace"},
            ],
        },
        {"id": 1, "tabPerimetres": [{"id": "2137438", "nom": "Autre"}]},
    ]
}


def _make_jwt(payload: dict) -> str:
    """Build a fake JWT with the given payload — signature is not verified by the client."""

    def _b64(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{_b64({'alg': 'RS512'})}.{_b64(payload)}.signature"


TOKEN = _make_jwt({"phpSessionId": PHP_SESSION_ID})


def _make_client(token=TOKEN):
    with patch("core.sources.resana.resana_members_client.requests.Session"):
        client = ResanaMembersClient(token, BASE_URL)
    client.session = MagicMock()
    return client


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_init_sets_access_token_cookie():
    """Constructor sets the interstis_access cookie with the provided token."""
    with patch(
        "core.sources.resana.resana_members_client.requests.Session"
    ) as mock_session:
        ResanaMembersClient(TOKEN, BASE_URL)

    mock_session.return_value.cookies.set.assert_any_call("interstis_access", TOKEN)


def test_init_sets_php_session_id_cookie_from_token_claim():
    """Constructor extracts phpSessionId from the JWT and sets it as PHPSESSID."""
    with patch(
        "core.sources.resana.resana_members_client.requests.Session"
    ) as mock_session:
        ResanaMembersClient(TOKEN, BASE_URL)

    mock_session.return_value.cookies.set.assert_any_call("PHPSESSID", PHP_SESSION_ID)


def test_init_raises_when_token_has_no_php_session_id():
    """Constructor raises MissingPhpSessionId when the JWT lacks the phpSessionId claim."""
    token_without_claim = _make_jwt({"sub": 123})

    with pytest.raises(MissingPhpSessionId):
        ResanaMembersClient(token_without_claim, BASE_URL)


def test_init_sets_xhr_header():
    """Constructor adds the X-Requested-With: XMLHttpRequest header."""
    with patch(
        "core.sources.resana.resana_members_client.requests.Session"
    ) as mock_session:
        ResanaMembersClient(TOKEN, BASE_URL)

    assert (
        mock_session.return_value.headers.__setitem__.call_args_list[0][0][1]
        == "XMLHttpRequest"
    )


# ---------------------------------------------------------------------------
# get_workspaces() — Endpoint 0 (getOngletTrie)
# ---------------------------------------------------------------------------


def test_get_workspaces_fetches_csrf_from_generic_page():
    """get_workspaces() fetches the CSRF token from the slug-less /public/perimetre page."""
    client = _make_client()
    client.session.get.return_value.text = _CSRF_HTML
    client.session.post.return_value.json.return_value = _ONGLET_TRIE_RESPONSE

    client.get_workspaces()

    client.session.get.assert_called_once_with(
        f"{BASE_URL}/public/perimetre", timeout=30
    )
    assert client.session.headers.__setitem__.call_args_list[-1][0] == (
        "X-CSRF-Token",
        "abc123def",
    )


def test_get_workspaces_flattens_tabs_into_slug_name_pairs():
    """get_workspaces() flattens all tabData[].tabPerimetres into {slug, name} dicts."""
    client = _make_client()
    client.session.get.return_value.text = _CSRF_HTML
    client.session.post.return_value.json.return_value = _ONGLET_TRIE_RESPONSE

    result = client.get_workspaces()

    assert result == [
        {"slug": "2137428", "name": "Coucou"},
        {"slug": "2137419", "name": "TEST Worskspace"},
        {"slug": "2137438", "name": "Autre"},
    ]


def test_get_workspaces_posts_to_get_onglet_trie():
    """get_workspaces() POSTs to /public/perimetre/getOngletTrie with no body params."""
    client = _make_client()
    client.session.get.return_value.text = _CSRF_HTML
    client.session.post.return_value.json.return_value = _ONGLET_TRIE_RESPONSE

    client.get_workspaces()

    client.session.post.assert_called_once_with(
        f"{BASE_URL}/public/perimetre/getOngletTrie", timeout=30
    )


# ---------------------------------------------------------------------------
# find_slug_by_workspace_name()
# ---------------------------------------------------------------------------


def test_find_slug_by_workspace_name_returns_matching_slug():
    """find_slug_by_workspace_name() returns the PHP slug for an exact name match."""
    client = _make_client()
    client.session.get.return_value.text = _CSRF_HTML
    client.session.post.return_value.json.return_value = _ONGLET_TRIE_RESPONSE

    result = client.find_slug_by_workspace_name("TEST Worskspace")

    assert result == "2137419"


def test_find_slug_by_workspace_name_returns_none_when_not_found():
    """find_slug_by_workspace_name() returns None when no workspace matches the name."""
    client = _make_client()
    client.session.get.return_value.text = _CSRF_HTML
    client.session.post.return_value.json.return_value = _ONGLET_TRIE_RESPONSE

    result = client.find_slug_by_workspace_name("Unknown")

    assert result is None


# ---------------------------------------------------------------------------
# list_workspace_members() — Endpoint 4 (listerUtilisateursAdminDroits)
# ---------------------------------------------------------------------------


def test_list_workspace_members_visits_consulter_page_first():
    """list_workspace_members() visits consulter/{slug} first to prime the server-side context."""
    client = _make_client()
    client.session.get.return_value.text = _CSRF_HTML
    client.session.post.return_value.json.return_value = {}

    client.list_workspace_members(SLUG)

    client.session.get.assert_called_once_with(
        f"{BASE_URL}/public/perimetre/consulter/{SLUG}", timeout=30
    )


def test_list_workspace_members_posts_perimetre_id():
    """list_workspace_members() POSTs perimetre_id=slug to listerUtilisateursAdminDroits."""
    client = _make_client()
    client.session.get.return_value.text = _CSRF_HTML
    client.session.post.return_value.json.return_value = {}

    client.list_workspace_members(SLUG)

    client.session.post.assert_called_once_with(
        f"{BASE_URL}/public/perimetre/listerUtilisateursAdminDroits",
        data={"perimetre_id": SLUG},
        timeout=30,
    )


def test_list_workspace_members_extracts_name_firstname_email():
    """list_workspace_members() maps each entry's nested utilisateur to {name, firstName, email}."""
    client = _make_client()
    client.session.get.return_value.text = _CSRF_HTML
    client.session.post.return_value.json.return_value = {
        "DUPONT_Jean_1234567": {
            "utilisateur": {
                "nom": "Dupont",
                "prenom": "Jean",
                "mail_inscription": "jean.dupont@example.test",
            },
            "profil_droit": "10821219",
        }
    }

    result = client.list_workspace_members(SLUG)

    assert result == [
        {
            "name": "Dupont",
            "firstName": "Jean",
            "email": "jean.dupont@example.test",
        }
    ]


def test_list_workspace_members_empty_when_no_members():
    """list_workspace_members() returns an empty list when the API returns no entries."""
    client = _make_client()
    client.session.get.return_value.text = _CSRF_HTML
    client.session.post.return_value.json.return_value = {}

    result = client.list_workspace_members(SLUG)

    assert result == []
