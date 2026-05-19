"""HTTP client for the Interstis GED API."""

from django.conf import settings

import requests

PAGE_SIZE = 500
CHUNK_SIZE = 8192


class InterstisClient:
    def __init__(self):
        self.session = None
        self.token = None

    def authenticate(self):
        self.session = requests.Session()
        response = self.session.post(
            settings.RESANA_AUTH_ENDPOINT,
            {
                "mail_inscription": settings.RESANA_AUTH_USER,
                "password": settings.RESANA_AUTH_PASSWORD,
                "perimetre_id": "",
                "information_id": "",
                "new_licence": "",
                "choix_formule": "",
                "id_licence": "",
                "parsec_password_derive": "",
                "langue": "",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            allow_redirects=False,
        )
        response.raise_for_status()
        self.token = response.cookies.get("interstis_access")
        self.session.headers["Authorization"] = f"Bearer {self.token}"

    def _ensure_authenticated(self):
        if not self.token:
            self.authenticate()

    def _get_paginated(self, url: str, extra_params: dict | None = None) -> list[dict]:
        self._ensure_authenticated()
        results = []
        page = 1
        while True:
            params = {"page": page, "itemsPerPage": PAGE_SIZE, **(extra_params or {})}
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            results.extend(data.get("hydra:member", []))
            if len(results) >= data.get("hydra:totalItems", 0):
                break
            page += 1
        return results

    def get_workspaces(self) -> list[dict]:
        return self._get_paginated(settings.RESANA_API_ENDPOINT + "/api/workspaces")

    def explore(self, uuid: str) -> list[dict]:
        return self._get_paginated(
            f"{settings.RESANA_API_ENDPOINT}/api/targets/{uuid}/explore"
        )

    def download_file(self, uuid: str, destination_path: str) -> None:
        self._ensure_authenticated()
        url = f"{settings.RESANA_API_ENDPOINT}/api/targets/{uuid}/download"
        with self.session.get(url, stream=True, timeout=60) as response:
            response.raise_for_status()
            with open(destination_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    f.write(chunk)
