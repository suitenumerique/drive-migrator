"""DriveDestinationBackend — uploads a workspace to La Suite Drive."""

import csv
import os
import time
import uuid
from dataclasses import dataclass, field

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from celery.utils.log import get_task_logger
from requests.exceptions import HTTPError

from core.backends.destination import (
    MEMBERS_CSV_FILENAME,
    AbstractDestinationBackend,
)
from core.destinations.drive.drive_backend import (
    DriveBackend,
    DriveServiceAccountBackend,
    DriveUserTokenBackend,
    get_file_rejection_code,
)
from core.mails_manager import MailsManager
from core.metrics import DRIVE_UPLOAD_REJECTIONS
from core.models import Workspace
from core.processing.integrity import Stage

logger = get_task_logger(__name__)

_UPLOAD_STATE_PENDING = "pending"
_UPLOAD_STATE_ANALYZING = "analyzing"
_UPLOAD_STATE_READY = "ready"
# Set by Drive when the analyzer rejects the size (413): still downloadable.
_UPLOAD_STATE_TOO_LARGE_TO_ANALYZE = "file_too_large_to_analyze"


@dataclass
class _UploadLog:
    """What the last export() of a workspace sent to Drive."""

    workspace_id: str
    backend: DriveBackend
    root_id: str | None = None
    # Local path relative to the workspace root -> Drive item id.
    items: dict[str, str] = field(default_factory=dict)
    # Files Drive refused, in the Workspace.upload_errors format.
    rejected: list[dict] = field(default_factory=list)


class DriveDestinationBackend(AbstractDestinationBackend):
    """
    Destination backend that creates a workspace in La Suite Drive.

    Two auth modes are supported via DRIVE_AUTH_MODE:
    - "service_account" (default): uses OAuth2 client_credentials grant and
      /external_api/v1.0/. The migration user is explicitly shared as owner.
    - "user_token": uses the authenticated user's ProConnect token and /api/v1.0/.
      Drive automatically assigns ownership to the token holder, so the migration
      user is excluded from the sharing step.
    """

    name = "drive"
    label = "La Suite Drive"

    def __init__(self):
        # Backends are cached singletons (DestinationRegistry): the log is tagged
        # with its workspace and reset by every export().
        self._upload_log = None

    def _make_backend(self, user):
        auth_mode = getattr(settings, "DRIVE_AUTH_MODE", "service_account")
        if auth_mode == "user_token":
            return DriveUserTokenBackend(user)
        return DriveServiceAccountBackend()

    def export(self, workspace, user, local_folder_path: str) -> None:
        backend = self._make_backend(user)
        self._upload_log = _UploadLog(workspace_id=workspace.id, backend=backend)

        root = backend.create_folder(workspace.title)
        root_id = root["id"]
        self._upload_log.root_id = root_id
        workspace.set_destination_metadata("drive", {"workspace_id": root_id})

        csv_path = self._write_users_csv(workspace, local_folder_path)
        try:
            self._upload_tree(backend, local_folder_path, root_id)
        finally:
            if csv_path:
                os.remove(csv_path)
            workspace.upload_errors = self._upload_log.rejected
            workspace.save(update_fields=["upload_errors"])
            # One event per run, without paths: file names may be personal data.
            if self._upload_log.rejected:
                logger.error(
                    "%s file(s) rejected by Drive: %s",
                    len(self._upload_log.rejected),
                    [
                        (rejected["item_id"], rejected["error"])
                        for rejected in self._upload_log.rejected
                    ],
                )

        source_paths = set(self._upload_log.items) - {MEMBERS_CSV_FILENAME}
        rejected_paths = {rejected["path"] for rejected in self._upload_log.rejected}
        if source_paths and source_paths <= rejected_paths:
            raise RuntimeError(
                f"All {len(source_paths)} file(s) were rejected by Drive for "
                f"workspace {workspace.id}"
            )

        if getattr(settings, "DRIVE_SHARE_MEMBERS", True):
            self._share_members(backend, workspace, root_id)

        title = _("Votre espace {title} est prêt sur La Suite Drive !").format(
            title=workspace.title
        )
        MailsManager().send_migration_mail(
            user, workspace, "drive_ready", {"title": title}
        )

        workspace.set_destination_status("drive", Workspace.Status.SUCCESS)
        workspace.save()

    def _write_users_csv(self, workspace, local_folder_path: str) -> str | None:
        """Write the shared-users listing into the local folder, like the archive export."""
        if not workspace.members:
            return None
        csv_path = os.path.join(local_folder_path, MEMBERS_CSV_FILENAME)
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerows(
                [m.get("name", ""), m.get("firstName", ""), m.get("email", "")]
                for m in workspace.members
            )
        return csv_path

    def _share_members(self, backend, workspace, root_id: str) -> None:
        """Share root_id with all relevant emails, respecting the auth mode."""
        auth_mode = getattr(settings, "DRIVE_AUTH_MODE", "service_account")
        migration_email = (
            workspace.migration_user.email
            if workspace.migration_user and workspace.migration_user.email
            else None
        )

        # Collect all emails to share, deduplicating across migration_user + members.
        # In user_token mode the token holder is already owner — skip their email.
        emails_to_skip = set()
        if auth_mode == "user_token" and migration_email:
            emails_to_skip.add(migration_email)

        emails_to_share = set()
        if migration_email and migration_email not in emails_to_skip:
            emails_to_share.add(migration_email)
        for member in workspace.members or []:
            email = member.get("email", "")
            if email and email not in emails_to_skip:
                emails_to_share.add(email)

        for email in emails_to_share:
            self._share_with_email(backend, root_id, email)

    def _share_with_email(self, backend, item_id: str, email: str) -> None:
        drive_user = backend.find_user_by_email(email)
        if drive_user:
            backend.share_with_user(item_id, drive_user["id"])
        else:
            backend.invite_by_email(item_id, email)

    def _upload_tree(
        self, backend, local_path: str, drive_parent_id: str, relative_dir: str = ""
    ) -> None:
        """Recursively create Drive folders and upload files from the local tree."""
        for entry in sorted(os.scandir(local_path), key=lambda e: e.name):
            relative_path = os.path.join(relative_dir, entry.name)
            if entry.is_dir():
                folder = backend.create_subfolder(entry.name, parent_id=drive_parent_id)
                self._upload_tree(backend, entry.path, folder["id"], relative_path)
            elif entry.is_file():
                # Logged before the request so a failed creation can still be
                # looked up on Drive by the integrity check.
                item_id = str(uuid.uuid4())
                self._upload_log.items[relative_path] = item_id
                size = entry.stat().st_size
                # On a failure, the last of these Sentry breadcrumbs names the file.
                logger.info("Uploading file %s (%s bytes)", item_id, size)
                try:
                    item = backend.create_file_item(
                        entry.name,
                        parent_id=drive_parent_id,
                        size=size,
                        item_id=item_id,
                    )
                    # Drive may have replaced a recovered pending item with a new id.
                    self._upload_log.items[relative_path] = item["id"]
                    backend.upload_to_s3(item["policy"], entry.path)
                    backend.notify_upload_ended(item["id"])
                except HTTPError as error:
                    # Only a refusal of this file is skipped: any other error
                    # would hit every file.
                    code = get_file_rejection_code(error)
                    if code is None:
                        raise
                    DRIVE_UPLOAD_REJECTIONS.labels(code).inc()
                    self._upload_log.rejected.append(
                        {
                            "path": relative_path,
                            "item_id": self._upload_log.items[relative_path],
                            "error": code,
                        }
                    )

    def check_integrity(
        self, workspace, local_folder_path: str, wait_for_analysis: bool
    ) -> dict:
        log = self._upload_log
        if log is None or log.workspace_id != workspace.id or log.root_id is None:
            return {"files": {}, "extra": []}

        rejections = {rejected["path"]: rejected["error"] for rejected in log.rejected}
        try:
            drive_files = self._list_drive_files(log.backend, log.root_id)
            # The listing hides pending items: look up the missing ones directly.
            items = {
                path: drive_files.pop(item_id, None) or log.backend.get_item(item_id)
                for path, item_id in log.items.items()
            }
            if wait_for_analysis:
                self._wait_for_analysis(log.backend, items)
        # Broad on purpose: whatever prevents re-reading Drive (expired token,
        # outage...), the report must say so instead of failing the task.
        except Exception as error:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            logger.warning("Drive integrity check failed: %s", error)
            return {
                "files": {
                    path: _without_none(
                        {
                            "item_id": item_id,
                            "stage": Stage.UNVERIFIED,
                            "error": rejections.get(path),
                        }
                    )
                    for path, item_id in log.items.items()
                },
                "extra": [],
                "error": str(error),
            }

        files = {}
        for path, item_id in log.items.items():
            local_file = os.path.join(local_folder_path, path)
            local_size = (
                os.path.getsize(local_file) if os.path.isfile(local_file) else None
            )
            files[path] = _integrity_entry(item_id, items[path], local_size)
            if path == MEMBERS_CSV_FILENAME:
                files[path]["generated"] = True
            if path in rejections:
                files[path]["error"] = rejections[path]

        extra = [
            _without_none(
                {
                    "item_id": item["id"],
                    "path": item["path"],
                    "upload_state": item.get("upload_state"),
                    "size": item.get("size"),
                }
            )
            for item in drive_files.values()
        ]
        return {"files": files, "extra": extra}

    def _list_drive_files(self, backend, folder_id: str, path: str = "") -> dict:
        """Map Drive item id -> file item (with its Drive path) under folder_id."""
        files = {}
        for child in backend.list_children(folder_id):
            child_path = os.path.join(path, child["title"])
            if child.get("type") == "folder":
                files.update(self._list_drive_files(backend, child["id"], child_path))
            else:
                files[child["id"]] = {**child, "path": child_path}
        return files

    def _wait_for_analysis(self, backend, items: dict) -> None:
        """Poll files still under malware analysis until they settle or time out."""
        deadline = time.monotonic() + settings.DRIVE_INTEGRITY_ANALYSIS_TIMEOUT
        while (
            any(_is_analyzing(item) for item in items.values())
            and time.monotonic() < deadline
        ):
            time.sleep(settings.DRIVE_INTEGRITY_ANALYSIS_POLL_INTERVAL)
            for path, item in items.items():
                if _is_analyzing(item):
                    items[path] = backend.get_item(item["id"])


def _is_analyzing(item: dict | None) -> bool:
    return item is not None and item.get("upload_state") == _UPLOAD_STATE_ANALYZING


def _without_none(entry: dict) -> dict:
    return {key: value for key, value in entry.items() if value is not None}


def _integrity_entry(item_id: str, item: dict | None, local_size: int | None) -> dict:
    """Classify one uploaded file from the Drive item found for it (None: unknown)."""
    if item is None:
        return {"item_id": item_id, "stage": Stage.NOT_CREATED}

    upload_state = item.get("upload_state")
    size = item.get("size")
    if upload_state == _UPLOAD_STATE_PENDING:
        stage = Stage.PENDING
    elif upload_state not in (
        _UPLOAD_STATE_READY,
        _UPLOAD_STATE_TOO_LARGE_TO_ANALYZE,
        _UPLOAD_STATE_ANALYZING,
    ):
        stage = Stage.NOT_READY
    elif None not in (local_size, size) and size != local_size:
        stage = Stage.SIZE_MISMATCH
    elif upload_state == _UPLOAD_STATE_ANALYZING:
        stage = Stage.ANALYSIS_UNFINISHED
    else:
        stage = Stage.OK

    return _without_none(
        {
            "item_id": item_id,
            "title": item.get("title"),
            "upload_state": upload_state,
            "size": size,
            "stage": stage,
        }
    )
