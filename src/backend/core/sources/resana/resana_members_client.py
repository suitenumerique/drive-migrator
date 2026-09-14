"""HTTP client for the Resana PHP portal — reads the workspace members list.

Uses the same interstis_access token as the documented GED API, but talks to the
undocumented internal PHP endpoints (reverse-engineered from the portal's XHR calls).
Limited to what's needed to populate Workspace.members; does not read per-file permissions.

The PHPSESSID and CSRF token both come from the resana-migrator bridge response
(see ResanaTokenManager), not from decoding the access token or scraping HTML.
"""

import requests

_REQUEST_TIMEOUT = 30
GESTIONNAIRE_CODE = "GESTIONNAIRE"


class ResanaMembersClient:
    """Reads the list of workspace members from the Resana PHP portal."""

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

    def get_workspaces(self) -> list[dict]:
        """Return all workspaces accessible to the current user as {slug, name} dicts."""
        resp = self.session.post(
            f"{self.base_url}/public/perimetre/getOngletTrie", timeout=_REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {"slug": perimetre["id"], "name": perimetre["nom"]}
            for tab in data.get("tabData", [])
            for perimetre in tab.get("tabPerimetres", [])
        ]

    def find_slug_by_workspace_name(self, name: str) -> str | None:
        """Return the PHP slug of the workspace whose name matches, or None."""
        for workspace in self.get_workspaces():
            if workspace["name"] == name:
                return workspace["slug"]
        return None

    def _fetch_raw_members(self, slug: str) -> dict:
        """Return the raw listerUtilisateursAdminDroits payload, keyed by member.

        Requires the workspace to first be placed in session via
        GET .../perimetre/consulter/{slug} — a legacy constraint unrelated to
        CSRF, so this GET stays even without HTML scraping.
        """
        resp = self.session.get(
            f"{self.base_url}/public/perimetre/consulter/{slug}",
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        resp = self.session.post(
            f"{self.base_url}/public/perimetre/listerUtilisateursAdminDroits",
            data={"perimetre_id": slug},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def list_workspace_members(self, slug: str) -> list[dict]:
        """Return workspace members as {name, firstName, email} dicts."""
        return [
            {
                "name": entry["utilisateur"].get("nom", ""),
                "firstName": entry["utilisateur"].get("prenom", ""),
                "email": entry["utilisateur"].get("mail_inscription", ""),
            }
            for entry in self._fetch_raw_members(slug).values()
        ]

    def get_workspaces_with_role(self) -> list[dict]:
        """Return all workspaces as {slug, name, role_code} dicts.

        listerMesEspacesV2 groups workspaces by role tab and resolves the
        current session's own role server-side (profilDroitCode), so no
        cross-referencing with a separate role catalog is needed.
        """
        resp = self.session.get(
            f"{self.base_url}/public/perimetre/listerMesEspacesV2",
            params={"sorting": "TRIE_GROUPE_UTILISATEUR"},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "slug": perimetre["id"],
                "name": perimetre["nom"],
                "role_code": perimetre["profilDroitCode"],
            }
            for tab in data.get("tabData", [])
            for perimetre in tab.get("tabPerimetres", [])
        ]
