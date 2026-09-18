"""Tests for ResanaLockClient: locks/unlocks a workspace and its folders during migration.

Same auth setup as ResanaMembersClient: the interstis_access/PHPSESSID cookies and the
X-CSRF-TOKEN header come from ResanaTokenManager, not from decoding the JWT or scraping HTML.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from core.sources.resana.resana_lock_client import ResanaLockClient, ResanaLockError

BASE_URL = "https://resana-web.example.test"
SLUG = "2137458"
PHP_SESSION_ID = "fdbcdafa71a19f7ef05c7562aef9cd29"
CSRF_TOKEN = "abc123def"
ACCESS_TOKEN = "the-interstis-access-token"

_GET_FOLDERS_RESPONSE = {
    "folders": [
        {"id": "12677502", "name": "A classer", "dossier_mere": None, "children": []},
        {
            "id": "12677577",
            "name": "Hébergement",
            "dossier_mere": None,
            "children": [
                {
                    "id": "12677600",
                    "name": "Sous-dossier",
                    "dossier_mere": "12677577",
                    "children": [],
                }
            ],
        },
    ]
}


def _make_client():
    """Build a ResanaLockClient with a mocked underlying requests.Session."""
    with patch("core.sources.resana.resana_lock_client.requests.Session"):
        client = ResanaLockClient(
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
        "core.sources.resana.resana_lock_client.requests.Session"
    ) as mock_session:
        ResanaLockClient(
            access_token=ACCESS_TOKEN,
            session_id=PHP_SESSION_ID,
            csrf_token=CSRF_TOKEN,
            base_url=BASE_URL,
        )

    mock_session.return_value.cookies.set.assert_any_call(
        "interstis_access", ACCESS_TOKEN
    )


def test_init_sets_php_session_id_cookie():
    """Constructor sets PHPSESSID from the session_id argument."""
    with patch(
        "core.sources.resana.resana_lock_client.requests.Session"
    ) as mock_session:
        ResanaLockClient(
            access_token=ACCESS_TOKEN,
            session_id=PHP_SESSION_ID,
            csrf_token=CSRF_TOKEN,
            base_url=BASE_URL,
        )

    mock_session.return_value.cookies.set.assert_any_call("PHPSESSID", PHP_SESSION_ID)


def test_init_sets_csrf_header():
    """Constructor sets X-CSRF-TOKEN from the csrf_token argument."""
    with patch(
        "core.sources.resana.resana_lock_client.requests.Session"
    ) as mock_session:
        ResanaLockClient(
            access_token=ACCESS_TOKEN,
            session_id=PHP_SESSION_ID,
            csrf_token=CSRF_TOKEN,
            base_url=BASE_URL,
        )

    mock_session.return_value.headers.__setitem__.assert_any_call(
        "X-CSRF-TOKEN", CSRF_TOKEN
    )


# ---------------------------------------------------------------------------
# get_top_level_folder_ids()
# ---------------------------------------------------------------------------


def test_get_top_level_folder_ids_calls_get_folders():
    """get_top_level_folder_ids() POSTs to dossier/getFolders for the given slug."""
    client = _make_client()
    client.session.post.return_value.json.return_value = _GET_FOLDERS_RESPONSE

    client.get_top_level_folder_ids(SLUG)

    client.session.post.assert_called_once_with(
        f"{BASE_URL}/public/dossier/getFolders",
        params={"slug": SLUG},
        data={"allFolders": "true"},
        timeout=30,
    )


def test_get_top_level_folder_ids_excludes_nested_folders():
    """Only root folders (dossier_mere is None) are returned: saveDroit/deverouilleDossier
    cascade to sub-folders on their own, so nested ids would be redundant."""
    client = _make_client()
    client.session.post.return_value.json.return_value = _GET_FOLDERS_RESPONSE

    ids = client.get_top_level_folder_ids(SLUG)

    assert ids == ["12677502", "12677577"]


def test_get_top_level_folder_ids_raises_on_http_error():
    client = _make_client()
    client.session.post.return_value.raise_for_status.side_effect = requests.HTTPError(
        "boom"
    )

    with pytest.raises(requests.HTTPError, match="boom"):
        client.get_top_level_folder_ids(SLUG)


# ---------------------------------------------------------------------------
# get_folder_access_owners()
# ---------------------------------------------------------------------------


def test_get_folder_access_owner_posts_to_get_all_dossier_droit():
    """get_folder_access_owners() POSTs dossierId/id_socket to dossier/getAllDossierDroit."""
    client = _make_client()
    folder_id = "12677577"
    client.session.post.return_value.json.return_value = {"type": None}

    client.get_folder_access_owners(SLUG, folder_id)

    client.session.post.assert_called_once_with(
        f"{BASE_URL}/public/dossier/getAllDossierDroit",
        params={"slug": SLUG},
        files={
            "dossierId": (None, folder_id),
            "id_socket": (None, "undefined"),
        },
        timeout=30,
    )


def test_get_folder_access_owner_returns_none_when_unrestricted():
    """A folder with no restriction returns type: null → None."""
    client = _make_client()
    client.session.post.return_value.json.return_value = {"type": None}

    assert client.get_folder_access_owners(SLUG, "12677577") is None


def test_get_folder_access_owner_returns_user_ids_when_restricted_to_users():
    """A folder restricted to a nominative list returns its selected user ids."""
    client = _make_client()
    client.session.post.return_value.json.return_value = {
        "type": "utilisateur",
        "selected": ["2040942"],
    }

    assert client.get_folder_access_owners(SLUG, "12677577") == ["2040942"]


def test_get_folder_access_owner_returns_none_for_other_restriction_types():
    """A group-restricted folder is out of scope for our own-account check → None."""
    client = _make_client()
    client.session.post.return_value.json.return_value = {
        "type": "groupe",
        "selected": ["10174"],
    }

    assert client.get_folder_access_owners(SLUG, "12677577") is None


# ---------------------------------------------------------------------------
# lock_workspace() / unlock_workspace()
# ---------------------------------------------------------------------------


def test_lock_workspace_calls_figer():
    client = _make_client()

    client.lock_workspace(SLUG)

    client.session.post.assert_called_once_with(
        f"{BASE_URL}/public/perimetre/figer",
        params={"slug": SLUG, "socket": "undefined", "peri": SLUG},
        timeout=30,
        allow_redirects=False,
    )


def test_unlock_workspace_calls_defiger():
    client = _make_client()

    client.unlock_workspace(SLUG)

    client.session.post.assert_called_once_with(
        f"{BASE_URL}/public/perimetre/defiger",
        params={"slug": SLUG, "socket": "undefined", "peri": SLUG},
        timeout=30,
        allow_redirects=False,
    )


# ---------------------------------------------------------------------------
# grant_folder_access() / release_folder_access()
# ---------------------------------------------------------------------------


def test_grant_folder_access_calls_save_droit():
    client = _make_client()
    client.session.post.return_value.json.return_value = {"success": True}
    folder_id = "12677577"
    user_id = 2040942

    client.grant_folder_access(SLUG, folder_id, user_id)

    client.session.post.assert_called_once_with(
        f"{BASE_URL}/public/dossier/saveDroit",
        params={"slug": SLUG},
        files={
            "type": (None, "utilisateur"),
            "tabId[]": (None, str(user_id)),
            "dossierId": (None, folder_id),
            "id_socket": (None, "undefined"),
        },
        timeout=30,
    )


def test_release_folder_access_calls_deverouille_dossier():
    client = _make_client()
    client.session.post.return_value.json.return_value = {"success": True}
    folder_id = "12677577"

    client.release_folder_access(SLUG, folder_id)

    client.session.post.assert_called_once_with(
        f"{BASE_URL}/public/dossier/deverouilleDossier",
        params={"slug": SLUG},
        files={
            "folderId": (None, folder_id),
            "id_socket": (None, "undefined"),
        },
        timeout=30,
    )


@pytest.mark.parametrize(
    "call", ["grant_folder_access", "release_folder_access"], ids=lambda c: c
)
def test_folder_access_calls_raise_on_unsuccessful_json(call):
    """A `{"success": false}` answer must not pass for a success."""
    client = _make_client()
    client.session.post.return_value.json.return_value = {"success": False}
    args = (
        (SLUG, "12677577", 2040942)
        if call == "grant_folder_access"
        else (SLUG, "12677577")
    )

    with pytest.raises(ResanaLockError):
        getattr(client, call)(*args)


@pytest.mark.parametrize(
    "call", ["grant_folder_access", "release_folder_access"], ids=lambda c: c
)
def test_folder_access_calls_raise_on_non_json_answer(call):
    """An expired session answers with the HTML login page: that's a failure too."""
    client = _make_client()
    client.session.post.return_value.json.side_effect = ValueError("not JSON")
    args = (
        (SLUG, "12677577", 2040942)
        if call == "grant_folder_access"
        else (SLUG, "12677577")
    )

    with pytest.raises(ResanaLockError):
        getattr(client, call)(*args)


# ---------------------------------------------------------------------------
# get_connected_user_id()
# ---------------------------------------------------------------------------


def test_get_connected_user_id_reads_environment_variables():
    """get_connected_user_id() returns utilisateurConnecte.id from getEnvironmentVariables."""
    client = _make_client()
    client.session.get.return_value.json.return_value = {
        "utilisateurConnecte": {"id": "2040942", "nom": "POC DINUM"},
        "exterieur": False,
    }

    assert client.get_connected_user_id() == "2040942"
    client.session.get.assert_called_once_with(
        f"{BASE_URL}/public/aide/getEnvironmentVariables",
        headers={"Accept": "application/json"},
        timeout=30,
    )


def test_get_connected_user_id_returns_a_string():
    """The id is compared to getAllDossierDroit's string ids, so it's always a str."""
    client = _make_client()
    client.session.get.return_value.json.return_value = {
        "utilisateurConnecte": {"id": 2040942}
    }

    assert client.get_connected_user_id() == "2040942"
