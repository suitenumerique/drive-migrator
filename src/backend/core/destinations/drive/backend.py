"""DriveDestinationBackend — uploads a workspace to La Suite Drive, file by file."""

import csv
import os
import tempfile
import time
import uuid

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from celery.utils.log import get_task_logger
from requests.exceptions import HTTPError, RequestException

from core.backends.destination import (
    MEMBERS_CSV_FILENAME,
    AbstractDestinationBackend,
    AccountPolicy,
    FileExportStatus,
    RejectedFile,
)
from core.destinations.drive.drive_backend import (
    DriveBackend,
    DriveServiceAccountBackend,
    DriveUserTokenBackend,
    FileRejection,
    RecoveredItemUploaded,
    get_file_rejection,
)
from core.mails_manager import MailsManager
from core.models import MigrationRun, Workspace
from core.processing.integrity import Stage
from core.processing.rejected_files import RejectedFilesArchive

logger = get_task_logger(__name__)

_UPLOAD_STATE_PENDING = "pending"
_UPLOAD_STATE_ANALYZING = "analyzing"
_UPLOAD_STATE_READY = "ready"
# Set by Drive when the analyzer rejects the size (413): still downloadable.
_UPLOAD_STATE_TOO_LARGE_TO_ANALYZE = "file_too_large_to_analyze"

# Code of a file lost to an error rather than refused: its error message may
# hold URLs, so the CSV gets this code instead.
_UPLOAD_FAILED = "upload_failed"

# pylint: disable-next=fixme
# TODO: French only until the backend ships translations (.po), then switch
# these texts to English msgids wrapped in gettext.
_REJECTION_REASONS = {
    FileRejection.EXTENSION_NOT_ALLOWED: "Extension de fichier non autorisée",
    FileRejection.TYPE_NOT_ALLOWED: "Type de fichier non autorisé",
    FileRejection.SIZE_EXCEEDED: "Fichier trop volumineux",
    FileRejection.SIZE_MISMATCH: "Fichier corrompu pendant le transfert",
    FileRejection.BLOCKED_UPSTREAM: "Fichier bloqué en amont de Drive (ex. pare-feu)",
    _UPLOAD_FAILED: "Fichier non reçu par La Suite Drive (erreur d'envoi)",
}

# Drive item ids are derived from the run's rows in this namespace, so that a
# file sent again (e.g. by a retried task) lands on the same Drive item.
_ITEM_ID_NAMESPACE = uuid.UUID("5b3f7c0e-9d2a-4e8b-a1f6-2c7d9e4b8a10")


class DriveDestinationBackend(AbstractDestinationBackend):
    """
    Destination backend that creates a workspace in La Suite Drive.

    Two auth modes are supported via DRIVE_AUTH_MODE:
    - "service_account" (default): uses OAuth2 client_credentials grant and
      /external_api/v1.0/. The migration user is explicitly shared as owner.
    - "user_token": uses the authenticated user's ProConnect token and /external_api/v1.0/.
      Drive automatically assigns ownership to the token holder, so the migration
      user is excluded from the sharing step.
    """

    name = "drive"
    label = "La Suite Drive"

    def __init__(self):
        # Backends are cached singletons (DestinationRegistry): one Drive client,
        # hence one token, for each run.
        self._client = None
        self._client_run_id = None

    def describe_rejection(self, code: str) -> str:
        return _REJECTION_REASONS.get(code) or super().describe_rejection(code)

    def get_account_policy(self) -> AccountPolicy:
        # The service account creates the items itself.
        if getattr(settings, "DRIVE_AUTH_MODE", "service_account") != "user_token":
            return AccountPolicy.NONE
        try:
            return AccountPolicy(settings.DRIVE_ACCOUNT_POLICY)
        except ValueError:
            logger.error(
                "Invalid DRIVE_ACCOUNT_POLICY %r, falling back to %s",
                settings.DRIVE_ACCOUNT_POLICY,
                AccountPolicy.USER_CREATED.value,
            )
            return AccountPolicy.USER_CREATED

    def user_account_exists(self, user) -> bool:
        return DriveUserTokenBackend(user).get_me() is not None

    def get_account_creation_url(self) -> str | None:
        if self.get_account_policy() != AccountPolicy.USER_CREATED:
            return None
        # Logging in to Drive once creates the account.
        return settings.DRIVE_FRONTEND_URL

    def _make_backend(self, user):
        auth_mode = getattr(settings, "DRIVE_AUTH_MODE", "service_account")
        if auth_mode == "user_token":
            return DriveUserTokenBackend(user)
        return DriveServiceAccountBackend()

    def _client_for(self, user, run: MigrationRun) -> DriveBackend:
        if self._client is None or self._client_run_id != run.id:
            self._client = self._make_backend(user)
            self._client_run_id = run.id
        return self._client

    # --- File-by-file export ---

    def open_export(self, user, run) -> None:
        """Create the root folder, then every planned folder, parents first."""
        workspace = run.workspace
        client = self._client_for(user, run)
        root_id = client.create_folder(workspace.title)["id"]
        workspace.set_destination_metadata("drive", {"workspace_id": root_id})
        workspace.save(update_fields=["destination_metadata"])

        folder_ids = {}
        for directory in run.dirs:
            parent, title = os.path.split(directory)
            folder = client.create_subfolder(
                title, parent_id=folder_ids.get(parent, root_id)
            )
            folder_ids[directory] = folder["id"]
        self._save_state(run, {"root_id": root_id, "folder_ids": folder_ids})

    def put_file(self, user, run, migration_file, local_path: str) -> None:
        state = run.destinations_state[self.name]
        folder = os.path.dirname(migration_file.path)
        migration_file.results[self.name] = _send_file(
            self._client_for(user, run),
            local_path,
            parent_id=state["folder_ids"].get(folder, state["root_id"]),
            item_id=_item_id(str(migration_file.id)),
        )

    def close_export(self, user, run) -> None:
        workspace = run.workspace
        client = self._client_for(user, run)
        state = run.destinations_state[self.name]
        if workspace.members:
            state["members_csv"] = self._send_members_csv(client, run, state["root_id"])
            self._save_state(run, state)

        file_results = self._file_results(run)
        # Before the mail, which links the archive.
        self._archive_lost_files(run, file_results)
        if file_results and not any(
            result["status"] == FileExportStatus.SENT for _, result in file_results
        ):
            raise RuntimeError(
                f"All {len(file_results)} file(s) were rejected or failed on Drive "
                f"for workspace {workspace.id}"
            )

        if getattr(settings, "DRIVE_SHARE_MEMBERS", True):
            self._share_members(client, workspace, state["root_id"])

        title = _("Votre espace {title} est prêt sur La Suite Drive !").format(
            title=workspace.title
        )
        MailsManager().send_migration_mail(
            user, workspace, "drive_ready", {"title": title}
        )

        workspace.set_destination_status("drive", Workspace.Status.SUCCESS)
        workspace.save()

    def abort_export(self, run) -> None:
        """Hand back the files Drive refused, even though the export failed."""
        if self.name in run.destinations_state:
            self._archive_lost_files(run, self._file_results(run))

    def _save_state(self, run, state: dict) -> None:
        run.destinations_state[self.name] = state
        MigrationRun.objects.filter(pk=run.pk).update(
            destinations_state=run.destinations_state
        )

    def _file_results(self, run) -> list[tuple[str, dict]]:
        """(path, result) of every source file handed to Drive."""
        return [
            (row.path, row.results[self.name])
            for row in run.files.iterator()
            if self.name in row.results
        ]

    def _send_members_csv(self, client, run, root_id: str) -> dict:
        """Upload the shared-users listing at the root: one email per line, with
        no header, so Drive can parse the file."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = os.path.join(tmp_dir, MEMBERS_CSV_FILENAME)
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerows([m.get("email", "")] for m in run.workspace.members)
            return _send_file(
                client,
                csv_path,
                parent_id=root_id,
                item_id=_item_id(f"{run.id}:{MEMBERS_CSV_FILENAME}"),
            )

    def _archive_lost_files(self, run, file_results: list[tuple[str, dict]]) -> None:
        """Hand the files Drive refused or failed to receive to the rejected files
        archive, members listing included."""
        members_csv = run.destinations_state[self.name].get("members_csv")
        if members_csv:
            file_results = [*file_results, (MEMBERS_CSV_FILENAME, members_csv)]
        lost = [
            RejectedFile(
                path,
                result["error"]
                if result["status"] == FileExportStatus.REJECTED
                else _UPLOAD_FAILED,
                result["item_id"],
            )
            for path, result in file_results
            if result["status"] != FileExportStatus.SENT
        ]
        workspace = run.workspace
        RejectedFilesArchive(workspace, self).build(run, lost)
        # One event per run, without paths nor error messages (they may hold
        # URLs): file names may be personal data.
        if lost:
            logger.error(
                "%s file(s) not received by Drive: %s",
                len(lost),
                [(rejected.item_id, rejected.code) for rejected in lost],
            )

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

    def check_integrity(self, workspace, run, wait_for_analysis: bool) -> dict:
        state = run.destinations_state.get(self.name) if run else None
        if not state:
            return {"files": {}, "extra": []}

        expected = self._expected_files(run)
        client = self._client_for(workspace.migration_user, run)
        try:
            drive_files = self._list_drive_files(client, state["root_id"])
            # The listing hides pending items: look up the missing ones directly.
            items = {
                path: drive_files.pop(result["item_id"], None)
                or client.get_item(result["item_id"])
                for path, (result, _, _) in expected.items()
            }
            if wait_for_analysis:
                self._wait_for_analysis(client, items)
        # Broad on purpose: whatever prevents re-reading Drive (expired token,
        # outage...), the report must say so instead of failing the task.
        except Exception as error:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            logger.warning("Drive integrity check failed: %s", error)
            return _unverified(expected, error)

        return _checked(expected, items, drive_files)

    def _expected_files(self, run) -> dict[str, tuple[dict, int | None, bool]]:
        """Path -> (upload result, downloaded size, generated by the migrator)
        of everything the run sent Drive."""
        expected = {
            row.path: (row.results[self.name], row.size, False)
            for row in run.files.iterator()
            if self.name in row.results
        }
        members_csv = run.destinations_state[self.name].get("members_csv")
        if members_csv:
            expected[MEMBERS_CSV_FILENAME] = (members_csv, None, True)
        return expected

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


def _item_id(name: str) -> str:
    return str(uuid.uuid5(_ITEM_ID_NAMESPACE, name))


def _is_unauthorized(error: RequestException) -> bool:
    return (
        isinstance(error, HTTPError)
        and getattr(error.response, "status_code", None) == 401
    )


def _send_file(client, local_path: str, parent_id: str, item_id: str) -> dict:
    """Create, upload and complete one Drive file, named after local_path.
    Return its result; raise only when Drive rejects our credentials."""
    size = os.path.getsize(local_path)
    # On a failure, the last of these Sentry breadcrumbs names the file.
    logger.info("Uploading file %s (%s bytes)", item_id, size)
    try:
        try:
            item = client.create_file_item(
                os.path.basename(local_path),
                parent_id=parent_id,
                size=size,
                item_id=item_id,
            )
        except RecoveredItemUploaded as error:
            # Sent by a former run of the file task: its state is checked
            # with the others once the run closes.
            logger.info("File %s was uploaded already", item_id)
            return {"status": FileExportStatus.SENT, "item_id": error.item["id"]}
        except HTTPError as error:
            rejection = _creation_rejection(client, error, parent_id)
            if rejection is None:
                raise
            return _rejected(item_id, rejection)
        # Drive may have replaced a recovered pending item with a new id.
        item_id = item["id"]
        try:
            client.upload_to_s3(item["policy"], local_path)
            client.notify_upload_ended(item_id)
        except HTTPError as error:
            rejection = get_file_rejection(error)
            if rejection is None:
                raise
            return _rejected(item_id, rejection)
    except RequestException as error:
        if _is_unauthorized(error):
            raise
        # The error's message may hold its URL, hence the file name.
        logger.warning(
            "Upload of file %s failed: %s %s",
            item_id,
            type(error).__name__,
            getattr(error.response, "status_code", ""),
        )
        return {
            "status": FileExportStatus.FAILED,
            "item_id": item_id,
            "error": str(error),
        }
    return {"status": FileExportStatus.SENT, "item_id": item_id}


def _creation_rejection(client, error: HTTPError, parent_id: str):
    """The refusal of a file at its creation, if error is one. A block in front
    of Drive is the file's only when its folder can still be read."""
    rejection = get_file_rejection(error, at_creation=True)
    if rejection is FileRejection.BLOCKED_UPSTREAM and not _is_drive_reachable(
        client, parent_id
    ):
        return None
    return rejection


def _is_drive_reachable(client, folder_id: str) -> bool:
    """Probe the folder with a payload-less request: False if it is blocked
    too, so the block is not the file's."""
    try:
        client.get_item(folder_id)
    except RequestException:
        return False
    return True


def _rejected(item_id: str, rejection: FileRejection) -> dict:
    return {
        "status": FileExportStatus.REJECTED,
        "item_id": item_id,
        "error": rejection.value,
    }


def _unverified(expected: dict, error: Exception) -> dict:
    """Integrity result when Drive could not be re-read."""
    return {
        "files": {
            path: _without_none(
                {
                    "item_id": result["item_id"],
                    "stage": Stage.UNVERIFIED,
                    "error": result.get("error"),
                }
            )
            for path, (result, _, _) in expected.items()
        },
        "extra": [],
        "error": str(error),
    }


def _checked(expected: dict, items: dict, unmatched_drive_files: dict) -> dict:
    """Integrity result from the Drive item found for each expected file."""
    files = {}
    for path, (result, local_size, generated) in expected.items():
        entry = _integrity_entry(result["item_id"], items[path], local_size)
        if generated:
            entry["generated"] = True
        if result.get("error"):
            entry["error"] = result["error"]
        files[path] = entry

    extra = [
        _without_none(
            {
                "item_id": item["id"],
                "path": item["path"],
                "upload_state": item.get("upload_state"),
                "size": item.get("size"),
            }
        )
        for item in unmatched_drive_files.values()
    ]
    return {"files": files, "extra": extra}


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
