"""ResanaPermissionReader — reads file permissions from the Resana PHP API."""

from core.permissions.base import SourcePermissionReader
from core.permissions.models import (
    CanonicalRole,
    NormalizedFilePermission,
    PermissionTarget,
    UserPermission,
)

_ROLE_MAP = {
    "10821219": CanonicalRole.MANAGE,  # GESTIONNAIRE
    "10821218": CanonicalRole.WRITE,  # CONTRIBUTEUR
    "10821217": CanonicalRole.READ,  # VISITEUR
}

# Organisation-wide group IDs (constant across all Resana environments)
_GROUP_CODES = {
    "10174": "AGENT",
    "10176": "PARTENAIRE",
}


class ResanaPermissionReader(SourcePermissionReader):
    """Reads file permissions from the Resana PHP API and normalises them into the canonical model.

    Builds a lazy cache mapping GED UUIDs to NormalizedFilePermission by walking both the
    PHP folder tree and the GED file tree, matching entries by folder path and filename.
    """

    def __init__(self, php_client, ged_client, php_slug: str, ged_workspace_uuid: str):
        self.php_client = php_client
        self.ged_client = ged_client
        self.php_slug = php_slug
        self.ged_workspace_uuid = ged_workspace_uuid
        self._permission_cache: dict | None = None

    def get_workspace_members(self, workspace_id: str) -> list[UserPermission]:
        """Return all workspace members with their canonical role."""
        members = self.php_client.list_workspace_members(self.php_slug)
        return [
            UserPermission(
                email=m["email"], role=self._translate_role(m["profil_droit"])
            )
            for m in members
        ]

    def get_file_permission(self, file_id: str) -> NormalizedFilePermission | None:
        """Return the normalized permission for a GED file UUID, or None if not found."""
        return self._get_cache().get(file_id)

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def _get_cache(self) -> dict:
        if self._permission_cache is None:
            self._permission_cache = self._build_permission_cache()
        return self._permission_cache

    def _build_permission_cache(self) -> dict:
        """Build the full {ged_uuid → NormalizedFilePermission} map for the workspace."""
        php_folders = self.php_client.get_folders(self.php_slug)
        php_path_map = _resolve_php_folder_paths(php_folders)
        ged_file_map = self._build_ged_file_map("", self.ged_workspace_uuid)

        result = {}
        for folder in php_folders:
            folder_id = folder["id"]
            folder_path = php_path_map[folder_id]
            ged_files = ged_file_map.get(folder_path, {})
            if not ged_files:
                continue
            for php_file in self.php_client.get_ged_info(self.php_slug, folder_id):
                ged_uuid = ged_files.get(php_file["titre"])
                if ged_uuid:
                    result[ged_uuid] = self._resolve_permission(
                        php_file["id"], php_file["sharingType"]
                    )
        return result

    def _build_ged_file_map(self, path: str, folder_uuid: str) -> dict:
        """Return {folder_path → {filename → ged_uuid}} for the subtree rooted at folder_uuid."""
        members = self.ged_client.explore(folder_uuid)
        if not members:
            return {}
        raw = members[0]
        result = {}
        files = {
            (f"{f['name']}.{f['extension']}" if f.get("extension") else f["name"]): f[
                "uuid"
            ]
            for f in raw.get("files", [])
        }
        if files:
            result[path] = files
        for sub in raw.get("folders", []):
            sub_path = f"{path}/{sub['name']}" if path else sub["name"]
            result.update(self._build_ged_file_map(sub_path, sub["uuid"]))
        return result

    # ------------------------------------------------------------------
    # Permission translation
    # ------------------------------------------------------------------

    def _resolve_permission(
        self, php_id: int, sharing_type: str
    ) -> NormalizedFilePermission:
        """Translate a PHP sharingType into a NormalizedFilePermission."""
        if sharing_type == "UNLOCKED":
            return NormalizedFilePermission(target=PermissionTarget.ALL_MEMBERS)
        if sharing_type == "LOCKED":
            return NormalizedFilePermission(target=PermissionTarget.PRIVATE)
        details = self.php_client.get_file_details(self.php_slug, php_id)
        return self._resolve_restricted(php_id, details)

    def _resolve_restricted(
        self, php_id: int, details: dict
    ) -> NormalizedFilePermission:
        """Resolve a RESTRICTED sharingType into the appropriate canonical target."""
        info = details["information"]
        tab_restreints = details["tab_information_restreints"]

        if info.get("visible_profil_droit") == "GESTIONNAIRE_CONTRIBUTEUR":
            return NormalizedFilePermission(
                target=PermissionTarget.MANAGERS_CONTRIBUTORS
            )

        if tab_restreints:
            groups = [
                _GROUP_CODES[gid] for gid in tab_restreints if gid in _GROUP_CODES
            ]
            return NormalizedFilePermission(
                target=PermissionTarget.RESTRICTED_GROUPS, groups=groups
            )

        tab_profil = details["tab_profil_droit_sources"]
        users_data = self.php_client.list_users_by_file(self.php_slug, php_id)
        user_permissions = []
        for user in users_data:
            pds = user.get("profilDroitSourceObjet") or {}
            entry = tab_profil.get(pds.get("objet_source", ""))
            if not entry:
                continue
            role = self._translate_role(entry["profil_droit"])
            user_permissions.append(
                UserPermission(email=user["mail_inscription"], role=role)
            )
        return NormalizedFilePermission(
            target=PermissionTarget.SPECIFIC_USERS,
            user_permissions=user_permissions,
        )

    @staticmethod
    def _translate_role(profil_droit: str) -> CanonicalRole:
        """Map a Resana profil_droit code to a CanonicalRole, defaulting to READ."""
        return _ROLE_MAP.get(profil_droit, CanonicalRole.READ)


def _resolve_php_folder_paths(folders: list[dict]) -> dict:
    """Build {php_folder_id → relative_path} from a flat list using dossier_mere."""
    id_to_folder = {f["id"]: f for f in folders}
    paths: dict = {}

    def _resolve(folder_id: int) -> str:
        if folder_id in paths:
            return paths[folder_id]
        folder = id_to_folder[folder_id]
        parent_id = folder.get("dossier_mere")
        if parent_id is None:
            paths[folder_id] = folder["name"]
        else:
            parent_path = _resolve(parent_id)
            paths[folder_id] = f"{parent_path}/{folder['name']}"
        return paths[folder_id]

    for folder in folders:
        _resolve(folder["id"])
    return paths
