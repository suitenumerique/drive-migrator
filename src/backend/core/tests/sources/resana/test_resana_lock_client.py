"""Tests for ResanaLockClient: locks/unlocks a workspace and its folders during migration.

Same auth setup as ResanaMembersClient: the interstis_access/PHPSESSID cookies and the
X-CSRF-TOKEN header come from ResanaTokenManager, not from decoding the JWT or scraping HTML.
"""

from unittest.mock import MagicMock

import pytest
import requests

from core.retrying_session import RetryingSession
from core.sources.resana.resana_lock_client import (
    ResanaChildDroitError,
    ResanaLockClient,
    ResanaLockError,
    ResanaParentDroitError,
)

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


@pytest.fixture(autouse=True)
def _stub_retrying_session(monkeypatch):
    """Keep _make_client() argument-free: RetryingSession is replaced by a MagicMock."""
    monkeypatch.setattr(
        "core.sources.resana.resana_lock_client.RetryingSession",
        MagicMock(),
    )


def _make_client():
    """Build a ResanaLockClient with a mocked underlying RetryingSession."""
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


def test_init_sets_access_token_cookie(monkeypatch):
    """Constructor sets the interstis_access cookie with the provided token."""
    mock_session = MagicMock()
    monkeypatch.setattr(
        "core.sources.resana.resana_lock_client.RetryingSession",
        mock_session,
    )
    ResanaLockClient(
        access_token=ACCESS_TOKEN,
        session_id=PHP_SESSION_ID,
        csrf_token=CSRF_TOKEN,
        base_url=BASE_URL,
    )

    mock_session.return_value.cookies.set.assert_any_call(
        "interstis_access", ACCESS_TOKEN
    )


def test_init_sets_php_session_id_cookie(monkeypatch):
    """Constructor sets PHPSESSID from the session_id argument."""
    mock_session = MagicMock()
    monkeypatch.setattr(
        "core.sources.resana.resana_lock_client.RetryingSession",
        mock_session,
    )
    ResanaLockClient(
        access_token=ACCESS_TOKEN,
        session_id=PHP_SESSION_ID,
        csrf_token=CSRF_TOKEN,
        base_url=BASE_URL,
    )

    mock_session.return_value.cookies.set.assert_any_call("PHPSESSID", PHP_SESSION_ID)


def test_init_sets_csrf_header(monkeypatch):
    """Constructor sets X-CSRF-TOKEN from the csrf_token argument."""
    mock_session = MagicMock()
    monkeypatch.setattr(
        "core.sources.resana.resana_lock_client.RetryingSession",
        mock_session,
    )
    ResanaLockClient(
        access_token=ACCESS_TOKEN,
        session_id=PHP_SESSION_ID,
        csrf_token=CSRF_TOKEN,
        base_url=BASE_URL,
    )

    mock_session.return_value.headers.__setitem__.assert_any_call(
        "X-CSRF-TOKEN", CSRF_TOKEN
    )


def test_init_uses_a_retrying_session_with_resana_settings(monkeypatch):
    """Constructor builds a RetryingSession driven by the RESANA_RETRY_* settings."""
    mock_session = MagicMock()
    monkeypatch.setattr(
        "core.sources.resana.resana_lock_client.RetryingSession",
        mock_session,
    )
    ResanaLockClient(
        access_token=ACCESS_TOKEN,
        session_id=PHP_SESSION_ID,
        csrf_token=CSRF_TOKEN,
        base_url=BASE_URL,
    )

    mock_session.assert_called_once()
    assert mock_session.call_args[0][0] == "RESANA"


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
        data={"allFolders": "true", "perimeterId": SLUG},
        timeout=30,
        retry=True,
    )


def test_list_folders_includes_nested_folders_and_droit():
    """Children are listed, and a droit on the payload is kept."""
    client = _make_client()
    client.session.post.return_value.json.return_value = {
        "folders": [
            {
                "id": "12677577",
                "name": "Hébergement",
                "dossier_mere": None,
                "droit": {"type": "groupe", "tabId": ["10174"]},
                "children": [
                    {"id": "12677600", "name": "Sous-dossier", "children": []},
                ],
            }
        ]
    }

    assert client.list_folders(SLUG) == [
        {
            "id": "12677577",
            "name": "Hébergement",
            "dossier_mere": None,
            "droit": {"type": "groupe", "tabId": ["10174"]},
        },
        {
            "id": "12677600",
            "name": "Sous-dossier",
            "dossier_mere": "12677577",
        },
    ]


def test_list_folders_keeps_defaut_from_the_payload():
    """The portal default inbox is flagged with defaut so begin_export can skip it."""
    client = _make_client()
    client.session.post.return_value.json.return_value = {
        "folders": [
            {
                "id": "12677569",
                "name": "A classer",
                "dossier_mere": None,
                "defaut": "1",
                "droit": None,
                "children": [],
            }
        ]
    }

    assert client.list_folders(SLUG) == [
        {
            "id": "12677569",
            "name": "A classer",
            "dossier_mere": None,
            "droit": None,
            "defaut": "1",
        }
    ]


def test_list_folders_raises_when_perimeter_id_is_missing():
    """HTTP 200 with the portal error must not be treated as an empty workspace."""
    client = _make_client()
    client.session.post.return_value.json.return_value = {
        "error": {
            "message": ["perimeterId est manquant !"],
            "titre": "getFoldersAction",
        }
    }

    with pytest.raises(ResanaLockError, match="perimeterId"):
        client.list_folders(SLUG)


def test_grant_folder_access_raises_when_a_child_already_has_a_right():
    """HTTP 200 with the child-droit error is a failure: nothing was applied."""
    client = _make_client()
    client.session.post.return_value.json.return_value = {
        "error": {
            "message": "Un droit est déjà appliqué sur l'un des dossiers enfants !",
            "titre": "",
        }
    }

    with pytest.raises(ResanaChildDroitError, match="dossiers enfants"):
        client.grant_folder_access(SLUG, "12677577", "2040942")


def test_release_folder_access_raises_when_a_parent_is_locked():
    """HTTP 200 with the parent-locked error is a failure: nothing was applied."""
    client = _make_client()
    client.session.post.return_value.json.return_value = {
        "error": {
            "message": "Un dossier parent vérouillé bloque cette action.",
            "titre": "",
        }
    }

    with pytest.raises(ResanaParentDroitError, match="dossier parent"):
        client.release_folder_access(SLUG, "12677578")


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
        retry=True,
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
        retry=True,
    )


def test_unlock_workspace_calls_defiger():
    client = _make_client()

    client.unlock_workspace(SLUG)

    client.session.post.assert_called_once_with(
        f"{BASE_URL}/public/perimetre/defiger",
        params={"slug": SLUG, "socket": "undefined", "peri": SLUG},
        timeout=30,
        allow_redirects=False,
        retry=True,
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
        retry=True,
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
        retry=True,
    )


_GET_PERIMETER_ELEMENTS_DATA = {
    "idDossier": "12677637",
    "perimeterId": SLUG,
    "ged_scope": "inEspace",
    "uniqueJSON": "true",
    "dossier_mode": "true",
    "offcetDebut": "0",
    "offset": "0",
    "limit": "500",
    "noLimitNombre": "1",
}


def test_list_files_calls_get_perimeter_elements():
    client = _make_client()
    client.session.post.return_value.json.return_value = {
        "tabInformation": [{"id": 111, "name": "open.odt", "droit": None}]
    }

    client.list_files(SLUG, "12677637")

    client.session.post.assert_called_once_with(
        f"{BASE_URL}/public/information/getPerimeterElements",
        params={"slug": SLUG},
        data=_GET_PERIMETER_ELEMENTS_DATA,
        timeout=30,
        retry=True,
    )


def _page(start, count):
    return {
        "tabInformation": [
            {"id": file_id, "name": f"f{file_id}"}
            for file_id in range(start, start + count)
        ]
    }


def test_list_files_concatenates_full_pages():
    """A full page is followed by the next offset until a short page."""
    client = _make_client()

    def _post(_url, **kwargs):
        offset = int(kwargs["data"]["offset"])
        response = MagicMock()
        response.json.return_value = _page(offset, 500 if offset == 0 else 1)
        response.raise_for_status = MagicMock()
        return response

    client.session.post.side_effect = _post

    listed = client.list_files(SLUG, "12677637")

    assert [item["id"] for item in listed] == [str(file_id) for file_id in range(501)]
    assert client.session.post.call_count == 2


def test_list_files_raises_when_a_page_is_repeated():
    """An offset the portal ignores must not loop or drop the rest of the files."""
    client = _make_client()
    client.session.post.return_value.json.return_value = _page(0, 500)

    with pytest.raises(ResanaLockError, match="repeated a page"):
        client.list_files(SLUG, "12677637")


def test_list_files_reads_tab_information():
    """The portal lists files under tabInformation, not informations."""
    client = _make_client()
    client.session.post.return_value.json.return_value = {
        "tabInformation": [
            {
                "id": 222,
                "titre": "secret.odt",
                "droit": {"type": "utilisateur", "tabId": ["2040942"]},
                "verrouillage": True,
            }
        ]
    }

    assert client.list_files(SLUG, "12677637") == [
        {
            "id": "222",
            "name": "secret.odt",
            "droit": {"type": "utilisateur", "tabId": ["2040942"]},
            "verrouillage": True,
        }
    ]


def test_list_files_keeps_droit_and_nested_informations():
    """Files listed under a folder node are flattened, with their droit kept."""
    client = _make_client()
    client.session.post.return_value.json.return_value = {
        "tabInformation": [
            {
                "id": 111,
                "name": "open.odt",
                "droit": None,
            }
        ],
        "folders": [
            {
                "id": "12677637",
                "informations": [
                    {
                        "id": 222,
                        "titre": "secret.odt",
                        "droit": {"type": "utilisateur", "tabId": ["2040942"]},
                    }
                ],
            }
        ],
    }

    assert client.list_files(SLUG, "12677637") == [
        {"id": "111", "name": "open.odt", "droit": None},
        {
            "id": "222",
            "name": "secret.odt",
            "droit": {"type": "utilisateur", "tabId": ["2040942"]},
        },
    ]


def test_list_files_falls_back_to_actualites_when_tab_information_is_empty():
    """getPerimeterElements can still answer []: use the PHP GED folder listing."""
    client = _make_client()
    empty = MagicMock()
    empty.json.return_value = {"tabInformation": []}
    listed = MagicMock()
    listed.json.return_value = {
        "tabInformation": [
            {
                "id": 222,
                "titre": "secret.odt",
                "droit": {"type": "utilisateur", "tabId": ["2040942"]},
            }
        ]
    }
    client.session.post.side_effect = [empty, listed]

    assert client.list_files(SLUG, "12677637") == [
        {
            "id": "222",
            "name": "secret.odt",
            "droit": {"type": "utilisateur", "tabId": ["2040942"]},
        }
    ]
    assert client.session.post.call_args_list[1][0] == (
        f"{BASE_URL}/public/information/listeActualitesPerimetre",
    )
    assert client.session.post.call_args_list[1][1]["data"] == {
        "offcetDebut": "0",
        "uniqueJSON": "true",
        "idDossier": "12677637",
        "dossier_mode": "true",
        "noLimitNombre": "1",
    }


def test_list_files_parses_information_ids_from_html_listing():
    """uniqueJSON can still be the PHP GED HTML snippet with li_information ids."""
    client = _make_client()
    empty = MagicMock()
    empty.json.return_value = {"tabInformation": []}
    html = MagicMock()
    html.json.side_effect = ValueError("not JSON")
    html.text = (
        '<ul><li id="li_information_222"></li>'
        '<li id="li_information_222"></li>'
        '<li id="li_information_333"></li></ul>'
    )
    client.session.post.side_effect = [empty, html]

    assert client.list_files(SLUG, "12677637") == [
        {"id": "222", "name": ""},
        {"id": "333", "name": ""},
    ]


def test_list_files_raises_when_the_payload_has_an_error():
    client = _make_client()
    client.session.post.return_value.json.return_value = {
        "error": {"message": ["perimeterId est manquant !"]}
    }

    with pytest.raises(ResanaLockError, match="perimeterId"):
        client.list_files(SLUG, "12677637")


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


# ---------------------------------------------------------------------------
# Retry on transient errors, through the real RetryingSession
# ---------------------------------------------------------------------------


def _real_client():
    return ResanaLockClient(
        access_token=ACCESS_TOKEN,
        session_id=PHP_SESSION_ID,
        csrf_token=CSRF_TOKEN,
        base_url=BASE_URL,
    )


def _status_response(status_code, payload=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    return response


@pytest.fixture(name="base_request")
def fixture_base_request(monkeypatch):
    """Mock the requests.Session.request wrapped by RetryingSession, without sleeping."""
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda *_args, **_kwargs: None)
    mock_request = MagicMock()
    monkeypatch.setattr(requests.Session, "request", mock_request)
    monkeypatch.setattr(
        "core.sources.resana.resana_lock_client.RetryingSession",
        RetryingSession,
    )
    yield mock_request


def test_get_top_level_folder_ids_is_retried_on_timeout(base_request):
    """getFolders only reads, so a timed out POST is replayed."""
    base_request.side_effect = [
        requests.Timeout("read timed out"),
        _status_response(200, {"folders": []}),
    ]

    assert not _real_client().get_top_level_folder_ids(SLUG)
    assert base_request.call_count == 2


def test_lock_workspace_is_retried_on_timeout(base_request):
    """figer sets a state the caller checks afterwards: safe to replay."""
    base_request.side_effect = [
        requests.Timeout("read timed out"),
        _status_response(302),
    ]

    _real_client().lock_workspace(SLUG)

    assert base_request.call_count == 2


def test_grant_folder_access_is_retried_on_server_error(base_request):
    """A second saveDroit keeps the saved original rights: safe to replay."""
    base_request.side_effect = [
        _status_response(502),
        _status_response(200, {"success": True}),
    ]

    _real_client().grant_folder_access(SLUG, "folder-id", "user-id")

    assert base_request.call_count == 2


def test_release_folder_access_is_retried_on_timeout(base_request):
    """deverouilleDossier restores the saved rights: safe to replay."""
    base_request.side_effect = [
        requests.Timeout("read timed out"),
        _status_response(200, {"success": True}),
    ]

    _real_client().release_folder_access(SLUG, "folder-id")

    assert base_request.call_count == 2
