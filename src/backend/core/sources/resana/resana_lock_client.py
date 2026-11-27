"""HTTP client for the Resana PHP portal: locks a workspace and its folders during migration.

Uses the same interstis_access token as the documented GED API, but talks to the
undocumented internal PHP endpoints (reverse-engineered from the portal's XHR calls).
Limited to what's needed to lock a workspace for the duration of a migration (#215):
freeze it against edits, list folders (and clear blocking droits when needed),
grant the migration account access to every folder, and list files the GED
API hides behind a file-level droit.
"""

import re

from celery.utils.log import get_task_logger

from core.retrying_session import RetryingSession

logger = get_task_logger(__name__)

_REQUEST_TIMEOUT = 30
_DOWNLOAD_TIMEOUT = 60
_CHUNK_SIZE = 8192
_FILE_PAGE_SIZE = 500


class ResanaLockError(Exception):
    """Raised when a PHP portal call answered but did not have the expected effect."""


class ResanaChildDroitError(ResanaLockError):
    """saveDroit refused because a descendant folder still has its own droit.

    The portal answers HTTP 200 with this error and applies nothing.
    """


class ResanaParentDroitError(ResanaLockError):
    """deverouilleDossier refused because an ancestor folder is still locked.

    The portal answers HTTP 200 with this error and applies nothing.
    """


# The portal answers HTTP 200 with this body (success is absent), applying nothing:
# {"error": {"message": "Un droit est déjà appliqué sur l'un des dossiers enfants !",
#            "titre": ""}}
# We match on the stable substring: the apostrophe in "l'un" is a straight quote.
_CHILD_DROIT_MESSAGE = "dossiers enfants"
# "Un dossier parent vérouillé bloque cette action."
_PARENT_LOCKED_MESSAGE = "dossier parent"


_LI_INFORMATION_ID = re.compile(r"""id=["']li_information_(\d+)""")
_FILE_LIST_KEYS = ("tabInformation", "informations", "files")


def _lists_of_files(container: dict) -> list:
    raws = []
    for key in _FILE_LIST_KEYS:
        value = container.get(key)
        if isinstance(value, list):
            raws.extend(value)
    return raws


def _information_item(raw: dict) -> dict | None:
    file_id = raw.get("id")
    if file_id is None:
        file_id = raw.get("id_information")
    if file_id is None:
        return None
    item = {
        "id": str(file_id),
        "name": raw.get("name") or raw.get("titre") or "",
    }
    if "droit" in raw:
        item["droit"] = raw["droit"]
    if "verrouillage" in raw:
        item["verrouillage"] = raw["verrouillage"]
    return item


def _collect_informations(payload: dict) -> list[dict]:
    """Flatten files out of a PHP GED listing JSON body."""
    raws = _lists_of_files(payload)
    for folder in payload.get("folders") or payload.get("tabDossier") or []:
        if isinstance(folder, dict):
            raws.extend(_lists_of_files(folder))
    listed = []
    seen = set()
    for raw in raws:
        if not isinstance(raw, dict):
            continue
        item = _information_item(raw)
        if item is None or item["id"] in seen:
            continue
        seen.add(item["id"])
        listed.append(item)
    return listed


def _informations_from_html(markup: str) -> list[dict]:
    """Pull information ids out of the PHP GED HTML listing."""
    listed = []
    seen = set()
    for file_id in _LI_INFORMATION_ID.findall(markup):
        if file_id in seen:
            continue
        seen.add(file_id)
        listed.append({"id": file_id, "name": ""})
    return listed


def _raise_unless_success(resp) -> None:
    """Raise unless the response is the portal's JSON `{"success": true}`.

    An expired session answers with the login page instead, which a bare
    raise_for_status() would let through as a success. A child-folder droit
    conflict is its own error: the call did not apply.
    """
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError as exc:
        raise ResanaLockError(f"Non-JSON response from {resp.url}") from exc
    if isinstance(data, dict) and data.get("success") is True:
        return
    message = ""
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        message = str(error.get("message") or "")
    if _CHILD_DROIT_MESSAGE in message:
        raise ResanaChildDroitError(message)
    if _PARENT_LOCKED_MESSAGE in message:
        raise ResanaParentDroitError(message)
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
        # Every call is retried, POSTs included: the reads have no side effect, and
        # figer/defiger/saveDroit/deverouilleDossier set a state, so replaying one
        # that already went through leaves the workspace as intended. Observed on
        # Resana: a second saveDroit keeps the rights saved by the first one, which
        # deverouilleDossier restores.
        self.session = RetryingSession("RESANA", logger)
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

    def list_folders(self, slug: str) -> list[dict]:
        """Return every folder from getFolders, including ones nested in children.

        Each item has id, name, dossier_mere, and droit/defaut when the payload has them.
        A child that omits dossier_mere is recorded under the parent it was nested in.
        """
        resp = self.session.post(
            f"{self.base_url}/public/dossier/getFolders",
            params={"slug": slug},
            data={"allFolders": "true", "perimeterId": slug},
            timeout=_REQUEST_TIMEOUT,
            retry=True,
        )
        resp.raise_for_status()
        try:
            payload = resp.json()
        except ValueError as exc:
            raise ResanaLockError(f"Non-JSON response from {resp.url}") from exc
        if not isinstance(payload, dict) or payload.get("error"):
            raise ResanaLockError(f"Unsuccessful response from {resp.url}: {payload!r}")
        folders = payload.get("folders")
        if not isinstance(folders, list):
            raise ResanaLockError(f"Unsuccessful response from {resp.url}: {payload!r}")
        listed = []
        seen = set()

        def visit(raw: dict, parent_id: str | None) -> None:
            folder_id = raw["id"]
            if folder_id in seen:
                return
            seen.add(folder_id)
            dossier_mere = raw.get("dossier_mere")
            if dossier_mere is None:
                dossier_mere = parent_id
            folder = {
                "id": folder_id,
                "name": raw.get("name", ""),
                "dossier_mere": dossier_mere,
            }
            if "droit" in raw:
                folder["droit"] = raw["droit"]
            if "defaut" in raw:
                folder["defaut"] = raw["defaut"]
            listed.append(folder)
            for child in raw.get("children") or []:
                visit(child, folder_id)

        for raw in folders:
            visit(raw, None)
        return listed

    def get_top_level_folder_ids(self, slug: str) -> list[str]:
        """Return the ids of root folders only (dossier_mere is None).

        saveDroit/deverouilleDossier cascade to sub-folders. A file that
        already has its own droit is left untouched.
        """
        return [
            folder["id"]
            for folder in self.list_folders(slug)
            if folder.get("dossier_mere") is None
        ]

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
            retry=True,
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
            retry=True,
        )
        resp.raise_for_status()

    def unlock_workspace(self, slug: str) -> None:
        """Reverse lock_workspace (defiger). Same unverifiable 302 as figer."""
        resp = self.session.post(
            f"{self.base_url}/public/perimetre/defiger",
            params={"slug": slug, "socket": "undefined", "peri": slug},
            timeout=_REQUEST_TIMEOUT,
            allow_redirects=False,
            retry=True,
        )
        resp.raise_for_status()

    def grant_folder_access(self, slug: str, folder_id: str, user_id) -> None:
        """Restrict a folder to a nominative list containing only `user_id`.

        This overwrites whatever access was previously configured on the folder
        (and cascades to sub-folders). A file that already has its own droit
        keeps it. The restriction is left in place after the migration.
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
            retry=True,
        )
        _raise_unless_success(resp)

    def list_files(self, slug: str, folder_id: str) -> list[dict]:
        """Return the files (informations) in `folder_id`.

        getPerimeterElements answers `tabInformation` and needs a page size;
        without offset/limit it returns `[]` even when the folder has files.
        Pages of 500 are followed until a short page. A page whose ids were
        already returned means the portal ignored the offset. If that list is
        still empty, fall back to the PHP GED listing (listeActualitesPerimetre),
        which animators use to see locked files.
        """
        folder_id = str(folder_id)
        listed = []
        seen = set()
        offset = 0
        while True:
            payload = self._post_file_listing(
                f"{self.base_url}/public/information/getPerimeterElements",
                slug,
                {
                    "idDossier": folder_id,
                    "perimeterId": slug,
                    "ged_scope": "inEspace",
                    "uniqueJSON": "true",
                    "dossier_mode": "true",
                    "offcetDebut": str(offset),
                    "offset": str(offset),
                    "limit": str(_FILE_PAGE_SIZE),
                    "noLimitNombre": "1",
                },
            )
            if payload.get("error"):
                raise ResanaLockError(
                    f"Unsuccessful response from getPerimeterElements: {payload!r}"
                )
            page = _collect_informations(payload)
            page_ids = [item["id"] for item in page]
            if page_ids and set(page_ids) <= seen:
                raise ResanaLockError(
                    f"getPerimeterElements repeated a page at offset {offset}"
                )
            for item in page:
                if item["id"] in seen:
                    continue
                seen.add(item["id"])
                listed.append(item)
            if offset == 0 and not listed:
                return self._list_files_from_actualites(slug, folder_id)
            if len(page) < _FILE_PAGE_SIZE:
                return listed
            offset += _FILE_PAGE_SIZE

    def _list_files_from_actualites(self, slug: str, folder_id: str) -> list[dict]:
        """List files the way the PHP GED folder view does."""
        resp = self.session.post(
            f"{self.base_url}/public/information/listeActualitesPerimetre",
            params={"slug": slug},
            data={
                "offcetDebut": "0",
                "uniqueJSON": "true",
                "idDossier": folder_id,
                "dossier_mode": "true",
                "noLimitNombre": "1",
            },
            timeout=_REQUEST_TIMEOUT,
            retry=True,
        )
        resp.raise_for_status()
        try:
            payload = resp.json()
        except ValueError:
            return _informations_from_html(resp.text)
        if isinstance(payload, dict):
            if payload.get("error"):
                raise ResanaLockError(
                    f"Unsuccessful response from {resp.url}: {payload!r}"
                )
            items = _collect_informations(payload)
            return items or _informations_from_html(resp.text)
        if isinstance(payload, list):
            return _collect_informations({"tabInformation": payload})
        return _informations_from_html(resp.text)

    def _post_file_listing(self, url: str, slug: str, data: dict) -> dict:
        resp = self.session.post(
            url,
            params={"slug": slug},
            data=data,
            timeout=_REQUEST_TIMEOUT,
            retry=True,
        )
        resp.raise_for_status()
        try:
            payload = resp.json()
        except ValueError as exc:
            raise ResanaLockError(f"Non-JSON response from {resp.url}") from exc
        if not isinstance(payload, dict):
            raise ResanaLockError(f"Unsuccessful response from {resp.url}: {payload!r}")
        return payload

    def release_folder_access(self, slug: str, folder_id: str) -> None:
        """Clear the folder's current droit via deverouilleDossier.

        Used at the start of a migration to remove a pre-existing restriction
        that would hide files from the migration account.
        """
        resp = self.session.post(
            f"{self.base_url}/public/dossier/deverouilleDossier",
            params={"slug": slug},
            files={
                "folderId": (None, folder_id),
                "id_socket": (None, "undefined"),
            },
            timeout=_REQUEST_TIMEOUT,
            retry=True,
        )
        _raise_unless_success(resp)
