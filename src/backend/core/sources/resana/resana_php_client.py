"""HTTP client for the Resana internal PHP API."""

import json
import re

import requests

_PAGE_SIZE = 50


class ResanaPhpClient:
    """HTTP client for the Resana internal PHP API (non-documented XHR endpoints)."""

    def __init__(self, token: str, base_url: str):
        self.base_url = base_url
        self._csrf_token = None
        self.session = requests.Session()
        self.session.cookies.set("interstis_access", token)
        self.session.headers["X-Requested-With"] = "XMLHttpRequest"

    def _ensure_csrf(self, slug: str) -> None:
        if self._csrf_token:
            return
        resp = self.session.get(
            f"{self.base_url}/public/perimetre/consulter/{slug}", timeout=30
        )
        resp.raise_for_status()
        self._csrf_token = resp.cookies.get("CSRF-TOKEN")
        self.session.headers["X-CSRF-Token"] = self._csrf_token

    def get_folders(self, slug: str) -> list[dict]:
        """Return all PHP folders for the workspace, with their IDs and parent references."""
        self._ensure_csrf(slug)
        resp = self.session.post(
            f"{self.base_url}/public/dossier/getFolders",
            params={"slug": slug},
            data={"allFolders": "true", "perimeterId": slug},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("folders", [])

    def get_ged_info(self, slug: str, folder_id: int, limit: int = 100) -> list[dict]:
        """Return all files in a PHP folder with their sharing type, paginating as needed."""
        self._ensure_csrf(slug)
        all_files = []
        offset = 0
        while True:
            resp = self.session.post(
                f"{self.base_url}/public/information/getGedInfo",
                params={"slug": slug},
                data={
                    "view": "folderGED",
                    "perimetreId": slug,
                    "folderId": folder_id,
                    "search": "",
                    "sortType": "TITRE_ASC",
                    "offset": str(offset),
                    "limit": str(limit),
                    "id_socket": "",
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            batch = data.get("gedTab", [])
            all_files.extend(batch)
            total = int(data.get("nb_info", 0))
            if len(all_files) >= total:
                break
            offset += limit
        return all_files

    def get_file_details(self, slug: str, php_file_id: int) -> dict:
        """Return parsed permission details for a file (HTML response with embedded JS vars)."""
        self._ensure_csrf(slug)
        resp = self.session.post(
            f"{self.base_url}/public/information/afficherProfilDroitInformation",
            params={"startAide": "0", "peri": slug},
            data={
                "juste_retour": "true",
                "horsPerimetre": "false",
                "perimetre_selectionne": slug,
                "tab_id_infos": f"[{php_file_id}]",
                "fonction_retour": "sendToVue",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return self._parse_file_details_html(resp.text)

    @staticmethod
    def _parse_file_details_html(html: str) -> dict:
        def _extract(var_name: str, default):
            m = re.search(
                rf"var {var_name} = JSON\.parse\('(.+?)'\);",
                html,
                re.DOTALL,
            )
            if not m:
                return default
            return json.loads(m.group(1).replace("\\'", "'"))

        return {
            "information": _extract("information", {}),
            "tab_profil_droit_sources": _extract("tabProfilDroitSources", {}),
            "tab_information_restreints": _extract("tabInformationRestreints", {}),
            "tab_profil_droits": _extract("tabProfilDroits", []),
        }

    def list_users_by_file(self, slug: str, php_file_id: int) -> list[dict]:
        """Return all users with explicit access to a file, paginating as needed.

        Resana API returns at most 50 users per page — page size is not configurable.
        """
        self._ensure_csrf(slug)
        all_users = []
        offset = 0
        while True:
            resp = self.session.post(
                f"{self.base_url}/public/utilisateur/listerUtilisateurByPerimetreAndGroupe",
                data={
                    "uniquementSelected": "true",
                    "id_information": php_file_id,
                    "id_perimetre": slug,
                    "offcetChargerUtilisateurs": offset,
                    "nom_prenom": "",
                    "dossier_id": "",
                },
                timeout=30,
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            all_users.extend(batch)
            if len(batch) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE
        return all_users

    def list_workspace_members(self, slug: str) -> list[dict]:
        """Return all workspace members with their role (profil_droit)."""
        self._ensure_csrf(slug)
        resp = self.session.post(
            f"{self.base_url}/public/perimetre/listerUtilisateursAdminDroits",
            data={"perimetre_id": slug},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
