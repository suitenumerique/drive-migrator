"""Tests for ResanaMembersClient — reads workspace members from the Resana PHP portal.

The access token, PHPSESSID and CSRF token are all sourced from the resana-migrator
bridge response (see ResanaTokenManager), not scraped from JWT claims or HTML pages.
"""

from unittest.mock import MagicMock, patch

import pytest

from core.sources.resana.resana_members_client import ResanaMembersClient

BASE_URL = "https://resana-web.example.test"
SLUG = "2137419"
PHP_SESSION_ID = "fdbcdafa71a19f7ef05c7562aef9cd29"
CSRF_TOKEN = "abc123def"
ACCESS_TOKEN = "the-interstis-access-token"

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


def _make_client():
    """Build a ResanaMembersClient with a mocked underlying requests.Session."""
    with patch("core.sources.resana.resana_members_client.requests.Session"):
        client = ResanaMembersClient(
            access_token=ACCESS_TOKEN,
            session_id=PHP_SESSION_ID,
            csrf_token=CSRF_TOKEN,
            base_url=BASE_URL,
        )
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
        ResanaMembersClient(
            access_token=ACCESS_TOKEN,
            session_id=PHP_SESSION_ID,
            csrf_token=CSRF_TOKEN,
            base_url=BASE_URL,
        )

    mock_session.return_value.cookies.set.assert_any_call(
        "interstis_access", ACCESS_TOKEN
    )


def test_init_sets_php_session_id_cookie_from_argument():
    """Constructor sets PHPSESSID from the session_id argument, not a decoded JWT."""
    with patch(
        "core.sources.resana.resana_members_client.requests.Session"
    ) as mock_session:
        ResanaMembersClient(
            access_token=ACCESS_TOKEN,
            session_id=PHP_SESSION_ID,
            csrf_token=CSRF_TOKEN,
            base_url=BASE_URL,
        )

    mock_session.return_value.cookies.set.assert_any_call("PHPSESSID", PHP_SESSION_ID)


def test_init_sets_xhr_header():
    """Constructor adds the X-Requested-With: XMLHttpRequest header."""
    with patch(
        "core.sources.resana.resana_members_client.requests.Session"
    ) as mock_session:
        ResanaMembersClient(
            access_token=ACCESS_TOKEN,
            session_id=PHP_SESSION_ID,
            csrf_token=CSRF_TOKEN,
            base_url=BASE_URL,
        )

    mock_session.return_value.headers.__setitem__.assert_any_call(
        "X-Requested-With", "XMLHttpRequest"
    )


def test_init_sets_csrf_header_from_argument():
    """Constructor sets X-CSRF-TOKEN from the csrf_token argument, no HTML scraping involved."""
    with patch(
        "core.sources.resana.resana_members_client.requests.Session"
    ) as mock_session:
        ResanaMembersClient(
            access_token=ACCESS_TOKEN,
            session_id=PHP_SESSION_ID,
            csrf_token=CSRF_TOKEN,
            base_url=BASE_URL,
        )

    mock_session.return_value.headers.__setitem__.assert_any_call(
        "X-CSRF-TOKEN", CSRF_TOKEN
    )


def test_init_requires_keyword_arguments():
    """Positional args are rejected: four same-typed strings are too easy to transpose."""
    with patch("core.sources.resana.resana_members_client.requests.Session"):
        with pytest.raises(TypeError):
            ResanaMembersClient(  # pylint: disable=missing-kwoa,too-many-function-args
                ACCESS_TOKEN, PHP_SESSION_ID, CSRF_TOKEN, BASE_URL
            )


# ---------------------------------------------------------------------------
# get_workspaces() — Endpoint 0 (getOngletTrie)
# ---------------------------------------------------------------------------


def test_get_workspaces_posts_to_get_onglet_trie_without_a_prior_get():
    """get_workspaces() POSTs directly to getOngletTrie, no CSRF page fetch beforehand."""
    client = _make_client()
    client.session.post.return_value.json.return_value = _ONGLET_TRIE_RESPONSE

    client.get_workspaces()

    client.session.get.assert_not_called()
    client.session.post.assert_called_once_with(
        f"{BASE_URL}/public/perimetre/getOngletTrie", timeout=30
    )


def test_get_workspaces_flattens_tabs_into_slug_name_pairs():
    """get_workspaces() flattens all tabData[].tabPerimetres into {slug, name} dicts."""
    client = _make_client()
    client.session.post.return_value.json.return_value = _ONGLET_TRIE_RESPONSE

    result = client.get_workspaces()

    assert result == [
        {"slug": "2137428", "name": "Coucou"},
        {"slug": "2137419", "name": "TEST Worskspace"},
        {"slug": "2137438", "name": "Autre"},
    ]


# ---------------------------------------------------------------------------
# find_slug_by_workspace_name()
# ---------------------------------------------------------------------------


def test_find_slug_by_workspace_name_returns_matching_slug():
    """find_slug_by_workspace_name() returns the PHP slug for an exact name match."""
    client = _make_client()
    client.session.post.return_value.json.return_value = _ONGLET_TRIE_RESPONSE

    result = client.find_slug_by_workspace_name("TEST Worskspace")

    assert result == "2137419"


def test_find_slug_by_workspace_name_returns_none_when_not_found():
    """find_slug_by_workspace_name() returns None when no workspace matches the name."""
    client = _make_client()
    client.session.post.return_value.json.return_value = _ONGLET_TRIE_RESPONSE

    result = client.find_slug_by_workspace_name("Unknown")

    assert result is None


# ---------------------------------------------------------------------------
# list_workspace_members() — Endpoint 4 (listerUtilisateursAdminDroits)
# ---------------------------------------------------------------------------


def test_list_workspace_members_visits_consulter_page_first():
    """list_workspace_members() still visits consulter/{slug} first (mandatory legacy constraint)."""
    client = _make_client()
    client.session.post.return_value.json.return_value = {}

    client.list_workspace_members(SLUG)

    client.session.get.assert_called_once_with(
        f"{BASE_URL}/public/perimetre/consulter/{SLUG}", timeout=30
    )


def test_list_workspace_members_posts_perimetre_id():
    """list_workspace_members() POSTs perimetre_id=slug to listerUtilisateursAdminDroits."""
    client = _make_client()
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
    client.session.post.return_value.json.return_value = {}

    result = client.list_workspace_members(SLUG)

    assert result == []
