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

_LISTER_MES_ESPACES_LOCKED_RESPONSE = {
    "tabData": [
        {
            "tabPerimetres": [
                {"id": "2137454", "nom": "TEST Worskspace[1]"},
            ]
        }
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
# get_locked_workspaces() — Endpoint 0bis (listerMesEspaces, archiveUnique)
# ---------------------------------------------------------------------------


def test_get_locked_workspaces_posts_to_lister_mes_espaces_with_archive_unique():
    """get_locked_workspaces() POSTs archiveUnique=1 to listerMesEspaces."""
    client = _make_client()
    client.session.post.return_value.json.return_value = (
        _LISTER_MES_ESPACES_LOCKED_RESPONSE
    )

    client.get_locked_workspaces()

    client.session.post.assert_called_once_with(
        f"{BASE_URL}/public/perimetre/listerMesEspaces",
        data={"archiveUnique": "1"},
        timeout=30,
    )


def test_get_locked_workspaces_flattens_tabs_into_slug_name_pairs():
    """get_locked_workspaces() flattens tabData[].tabPerimetres like get_workspaces()."""
    client = _make_client()
    client.session.post.return_value.json.return_value = (
        _LISTER_MES_ESPACES_LOCKED_RESPONSE
    )

    result = client.get_locked_workspaces()

    assert result == [{"slug": "2137454", "name": "TEST Worskspace[1]"}]


# ---------------------------------------------------------------------------
# is_workspace_locked()
# ---------------------------------------------------------------------------


def test_is_workspace_locked_returns_true_when_slug_in_locked_list():
    """is_workspace_locked() is True when the slug appears in get_locked_workspaces()."""
    client = _make_client()
    client.session.post.return_value.json.return_value = (
        _LISTER_MES_ESPACES_LOCKED_RESPONSE
    )

    assert client.is_workspace_locked("2137454") is True


def test_is_workspace_locked_returns_false_when_slug_not_in_locked_list():
    """is_workspace_locked() is False when the slug is absent from the locked list."""
    client = _make_client()
    client.session.post.return_value.json.return_value = (
        _LISTER_MES_ESPACES_LOCKED_RESPONSE
    )

    assert client.is_workspace_locked("2137419") is False


def test_is_workspace_locked_returns_false_when_no_workspaces_locked():
    """is_workspace_locked() is False when the locked list is empty."""
    client = _make_client()
    client.session.post.return_value.json.return_value = {"tabData": []}

    assert client.is_workspace_locked("2137419") is False


def test_is_workspace_locked_posts_to_lister_mes_espaces():
    """is_workspace_locked() reuses get_locked_workspaces()'s endpoint, not a new one."""
    client = _make_client()
    client.session.post.return_value.json.return_value = {"tabData": []}

    client.is_workspace_locked("2137419")

    client.session.post.assert_called_once_with(
        f"{BASE_URL}/public/perimetre/listerMesEspaces",
        data={"archiveUnique": "1"},
        timeout=30,
    )


# ---------------------------------------------------------------------------
# find_slug_by_workspace_name()
# ---------------------------------------------------------------------------


def test_find_slug_by_workspace_name_returns_matching_slug():
    """find_slug_by_workspace_name() returns the PHP slug for an exact name match."""
    client = _make_client()
    client.session.post.return_value.json.return_value = _ONGLET_TRIE_RESPONSE

    result = client.find_slug_by_workspace_name("TEST Worskspace")

    assert result == "2137419"


def test_find_slug_by_workspace_name_returns_none_when_not_found_anywhere():
    """find_slug_by_workspace_name() returns None when no unlocked or locked workspace matches."""
    client = _make_client()

    def post_side_effect(url, **_kwargs):
        response = MagicMock()
        if url.endswith("/getOngletTrie"):
            response.json.return_value = _ONGLET_TRIE_RESPONSE
        elif url.endswith("/listerMesEspaces"):
            response.json.return_value = _LISTER_MES_ESPACES_LOCKED_RESPONSE
        return response

    client.session.post.side_effect = post_side_effect

    result = client.find_slug_by_workspace_name("Unknown")

    assert result is None


def test_find_slug_by_workspace_name_does_not_check_locked_workspaces_when_found_unlocked():
    """find_slug_by_workspace_name() short-circuits before hitting listerMesEspaces."""
    client = _make_client()
    client.session.post.return_value.json.return_value = _ONGLET_TRIE_RESPONSE

    client.find_slug_by_workspace_name("TEST Worskspace")

    client.session.post.assert_called_once_with(
        f"{BASE_URL}/public/perimetre/getOngletTrie", timeout=30
    )


def test_find_slug_by_workspace_name_falls_back_to_locked_workspaces():
    """find_slug_by_workspace_name() finds a locked workspace missing from getOngletTrie (#169)."""
    client = _make_client()

    def post_side_effect(url, **_kwargs):
        response = MagicMock()
        if url.endswith("/getOngletTrie"):
            response.json.return_value = _ONGLET_TRIE_RESPONSE
        elif url.endswith("/listerMesEspaces"):
            response.json.return_value = _LISTER_MES_ESPACES_LOCKED_RESPONSE
        return response

    client.session.post.side_effect = post_side_effect

    result = client.find_slug_by_workspace_name("TEST Worskspace[1]")

    assert result == "2137454"


# ---------------------------------------------------------------------------
# list_workspace_members() — listerUtilisateurByPerimetreAndGroupe
# ---------------------------------------------------------------------------


def test_list_workspace_members_visits_consulter_page_first():
    """list_workspace_members() still visits consulter/{slug} first (mandatory legacy constraint)."""
    client = _make_client()
    client.session.post.return_value.json.return_value = []

    client.list_workspace_members(SLUG)

    client.session.get.assert_called_once_with(
        f"{BASE_URL}/public/perimetre/consulter/{SLUG}", timeout=30
    )


def test_list_workspace_members_posts_id_perimetre_and_charger_all():
    """list_workspace_members() POSTs id_perimetre and chargerAllUtilisateurs=1."""
    client = _make_client()
    client.session.post.return_value.json.return_value = []

    client.list_workspace_members(SLUG)

    client.session.post.assert_called_once_with(
        f"{BASE_URL}/public/utilisateur/listerUtilisateurByPerimetreAndGroupe",
        data={"id_perimetre": SLUG, "chargerAllUtilisateurs": "1"},
        timeout=30,
    )


def test_list_workspace_members_extracts_name_firstname_email():
    """list_workspace_members() maps each flat entry to {name, firstName, email}."""
    client = _make_client()
    client.session.post.return_value.json.return_value = [
        {
            "id": "1234567",
            "nom": "Dupont",
            "prenom": "Jean",
            "mail_inscription": "jean.dupont@example.test",
        }
    ]

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
    client.session.post.return_value.json.return_value = []

    result = client.list_workspace_members(SLUG)

    assert result == []


# ---------------------------------------------------------------------------
# get_workspaces_with_role() — listerMesEspacesV2, role resolved server-side
# ---------------------------------------------------------------------------

_LISTER_MES_ESPACES_V2_RESPONSE = {
    "tabData": [
        {
            "id": "GESTIONNAIRE",
            "libelle": "Animateur",
            "tabPerimetres": [
                {
                    "id": "2137419",
                    "nom": "TEST Worskspace",
                    "profilDroitCode": "GESTIONNAIRE",
                    "profilDroitLibelle": "Animateur",
                }
            ],
        },
        {
            "id": "CONTRIBUTEUR",
            "libelle": "Contributeur",
            "tabPerimetres": [
                {
                    "id": "2137455",
                    "nom": "groupe1",
                    "profilDroitCode": "CONTRIBUTEUR",
                    "profilDroitLibelle": "Contributeur",
                }
            ],
        },
        {
            "id": "VISITEUR",
            "libelle": "Lecteur",
            "tabPerimetres": [
                {
                    "id": "2137456",
                    "nom": "groupe2",
                    "profilDroitCode": "VISITEUR",
                    "profilDroitLibelle": "Lecteur",
                }
            ],
        },
    ]
}


def test_get_workspaces_with_role_gets_lister_mes_espaces_v2_with_sorting_param():
    """get_workspaces_with_role() GETs listerMesEspacesV2 grouped by role tab."""
    client = _make_client()
    client.session.get.return_value.json.return_value = _LISTER_MES_ESPACES_V2_RESPONSE

    client.get_workspaces_with_role()

    client.session.get.assert_called_once_with(
        f"{BASE_URL}/public/perimetre/listerMesEspacesV2",
        params={"sorting": "TRIE_GROUPE_UTILISATEUR"},
        timeout=30,
    )


def test_get_workspaces_with_role_flattens_tabs_with_their_role_code():
    """get_workspaces_with_role() flattens all tabs into {slug, name, role_code} dicts."""
    client = _make_client()
    client.session.get.return_value.json.return_value = _LISTER_MES_ESPACES_V2_RESPONSE

    result = client.get_workspaces_with_role()

    assert result == [
        {"slug": "2137419", "name": "TEST Worskspace", "role_code": "GESTIONNAIRE"},
        {"slug": "2137455", "name": "groupe1", "role_code": "CONTRIBUTEUR"},
        {"slug": "2137456", "name": "groupe2", "role_code": "VISITEUR"},
    ]


def test_get_workspaces_with_role_empty_when_no_tabs():
    """get_workspaces_with_role() returns an empty list when the API returns no tabs."""
    client = _make_client()
    client.session.get.return_value.json.return_value = {}

    result = client.get_workspaces_with_role()

    assert result == []
