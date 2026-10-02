"""HTTP client for the Resana PHP portal: locks a workspace and its folders during migration.

Uses the same interstis_access token as the documented GED API, but talks to the
undocumented internal PHP endpoints (reverse-engineered from the portal's XHR calls).
Limited to what's needed to lock a workspace for the duration of a migration (#215):
freeze it against edits, and grant the migration account access to every folder so
the migration doesn't miss files it lacked rights to.
"""

import requests

_REQUEST_TIMEOUT = 30


class ResanaLockError(Exception):
    """Raised when a PHP portal call answered but did not have the expected effect."""


def _raise_unless_success(resp) -> None:
    """Raise unless the response is the portal's JSON `{"success": true}`.

    An expired session answers with the login page instead, which a bare
    raise_for_status() would let through as a success.
    """
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError as exc:
        raise ResanaLockError(f"Non-JSON response from {resp.url}") from exc
    if not isinstance(data, dict) or data.get("success") is not True:
        raise ResanaLockError(f"Unsuccessful response from {resp.url}: {data!r}")


class ResanaLockClient:
    """Locks/unlocks a workspace and grants/releases folder access during migration."""

    def __init__(
        self,
        *,
        access_token: str,
        session_id: str,
        csrf_token: str,
        base_url: str,
    ):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.cookies.set("interstis_access", access_token)
        self.session.cookies.set("PHPSESSID", session_id)
        self.session.headers["X-Requested-With"] = "XMLHttpRequest"
        self.session.headers["X-CSRF-TOKEN"] = csrf_token

    def get_connected_user_id(self) -> str:
        """Return the PHP user id of the account this session is logged in as."""
        resp = self.session.get(
            f"{self.base_url}/public/aide/getEnvironmentVariables",
            headers={"Accept": "application/json"},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return str(resp.json()["utilisateurConnecte"]["id"])

    def get_top_level_folder_ids(self, slug: str) -> list[str]:
        """Return the ids of root folders only (dossier_mere is None).

        saveDroit/deverouilleDossier both cascade to sub-folders and files on
        their own, so acting on root folders is enough to cover the whole tree.
        """
        resp = self.session.post(
            f"{self.base_url}/public/dossier/getFolders",
            params={"slug": slug},
            data={"allFolders": "true"},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        folders = resp.json().get("folders", [])
        return [f["id"] for f in folders if f.get("dossier_mere") is None]

    def get_folder_access_owners(self, slug: str, folder_id: str) -> list[str] | None:
        """Return the user ids a folder is restricted to, or None if unrestricted
        (or restricted some other way, e.g. a group, out of scope for our own check).
        """
        resp = self.session.post(
            f"{self.base_url}/public/dossier/getAllDossierDroit",
            params={"slug": slug},
            files={
                "dossierId": (None, folder_id),
                "id_socket": (None, "undefined"),
            },
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("type") != "utilisateur":
            return None
        return data.get("selected", [])

    def lock_workspace(self, slug: str) -> None:
        """Freeze the workspace (figer): archives it, blocking new content for everyone.

        figer answers with a bare 302 whether it worked or the session expired,
        so callers must check the resulting state themselves.
        """
        resp = self.session.post(
            f"{self.base_url}/public/perimetre/figer",
            params={"slug": slug, "socket": "undefined", "peri": slug},
            timeout=_REQUEST_TIMEOUT,
            allow_redirects=False,
        )
        resp.raise_for_status()

    def unlock_workspace(self, slug: str) -> None:
        """Reverse lock_workspace (defiger). Same unverifiable 302 as figer."""
        resp = self.session.post(
            f"{self.base_url}/public/perimetre/defiger",
            params={"slug": slug, "socket": "undefined", "peri": slug},
            timeout=_REQUEST_TIMEOUT,
            allow_redirects=False,
        )
        resp.raise_for_status()

    def grant_folder_access(self, slug: str, folder_id: str, user_id) -> None:
        """Restrict a folder to a nominative list containing only `user_id`.

        This overwrites whatever access was previously configured on the folder
        (and cascades to its sub-folders/files); release_folder_access restores
        the prior state afterwards.
        """
        resp = self.session.post(
            f"{self.base_url}/public/dossier/saveDroit",
            params={"slug": slug},
            files={
                "type": (None, "utilisateur"),
                "tabId[]": (None, str(user_id)),
                "dossierId": (None, folder_id),
                "id_socket": (None, "undefined"),
            },
            timeout=_REQUEST_TIMEOUT,
        )
        _raise_unless_success(resp)

    def release_folder_access(self, slug: str, folder_id: str) -> None:
        """Restore the folder's access to whatever it was before grant_folder_access."""
        resp = self.session.post(
            f"{self.base_url}/public/dossier/deverouilleDossier",
            params={"slug": slug},
            files={
                "folderId": (None, folder_id),
                "id_socket": (None, "undefined"),
            },
            timeout=_REQUEST_TIMEOUT,
        )
        _raise_unless_success(resp)
