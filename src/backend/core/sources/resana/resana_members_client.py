"""HTTP client for the Resana PHP portal — reads the workspace members list.

Uses the same interstis_access token as the documented GED API, but talks to the
undocumented internal PHP endpoints (reverse-engineered from the portal's XHR calls).
Limited to what's needed to populate Workspace.members; does not read per-file permissions.
"""

import base64
import json
import re

import requests

_REQUEST_TIMEOUT = 30
_CSRF_PATTERN = re.compile(r"var CSRFToken = '([a-f0-9]+)';")


class MissingPhpSessionId(Exception):
    """Raised when the access token JWT has no phpSessionId claim."""


def _extract_php_session_id(token: str) -> str:
    """Decode the JWT payload and return its phpSessionId claim.

    The PHP portal ties its session to this claim — the interstis_access cookie
    alone is accepted by the GED API but rejected (401) by the PHP portal without it.
    """
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload["phpSessionId"]
    except (IndexError, ValueError, KeyError) as exc:
        raise MissingPhpSessionId(
            "Access token JWT has no phpSessionId claim."
        ) from exc


class ResanaMembersClient:
    """Reads the list of workspace members from the Resana PHP portal."""

    def __init__(self, token: str, base_url: str):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.cookies.set("interstis_access", token)
        self.session.cookies.set("PHPSESSID", _extract_php_session_id(token))
        self.session.headers["X-Requested-With"] = "XMLHttpRequest"

    def _fetch_csrf_token(self, path: str) -> None:
        resp = self.session.get(f"{self.base_url}{path}", timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        match = _CSRF_PATTERN.search(resp.text)
        if not match:
            raise RuntimeError(f"CSRF token not found on {path}")
        self.session.headers["X-CSRF-Token"] = match.group(1)

    def get_workspaces(self) -> list[dict]:
        """Return all workspaces accessible to the current user as {slug, name} dicts."""
        self._fetch_csrf_token("/public/perimetre")
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

    def list_workspace_members(self, slug: str) -> list[dict]:
        """Return workspace members as {name, firstName, email} dicts."""
        self._fetch_csrf_token(f"/public/perimetre/consulter/{slug}")
        resp = self.session.post(
            f"{self.base_url}/public/perimetre/listerUtilisateursAdminDroits",
            data={"perimetre_id": slug},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return [
            {
                "name": entry["utilisateur"].get("nom", ""),
                "firstName": entry["utilisateur"].get("prenom", ""),
                "email": entry["utilisateur"].get("mail_inscription", ""),
            }
            for entry in resp.json().values()
        ]
