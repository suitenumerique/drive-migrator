"""Tests for DriveDestinationBackend."""

# pylint: disable=no-member  # django.core.mail.outbox is added by the locmem backend
# pylint: disable=redefined-outer-name  # pytest fixtures intentionally shadow outer names
# pylint: disable=unused-argument  # requesting the drive fixture scripts the API

import json
import logging
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.core import mail
from django.utils import timezone

import pytest
from cryptography.fernet import Fernet
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import HTTPError, Timeout

from core import factories
from core.backends.destination import (
    AbstractDestinationBackend,
    AccountPolicy,
)
from core.destinations.drive.backend import DriveDestinationBackend
from core.destinations.drive.drive_backend import FileRejection
from core.encryption import encrypt_token
from core.models import Workspace
from core.tests.destinations.drive.drive_api import DriveApi

pytestmark = pytest.mark.django_db

_CSV = "users_list_by_migrator.csv"


@pytest.fixture(autouse=True)
def drive_settings(settings, tmp_path):
    settings.DRIVE_API_BASE_URL = "https://drive.example.com"
    settings.DRIVE_OIDC_TOKEN_ENDPOINT = "https://oidc.example.com/token"
    settings.DRIVE_OIDC_CLIENT_ID = "client-id"
    settings.DRIVE_OIDC_CLIENT_SECRET = "client-secret"
    settings.DRIVE_AUTH_MODE = "service_account"
    settings.DRIVE_RETRY_MAX_ATTEMPTS = 1
    settings.DRIVE_SHARE_MEMBERS = True
    settings.APP_WORK_DIR = str(tmp_path / "work")
    settings.AWS_STORAGE_BUCKET_NAME = "bucket"
    settings.AWS_S3_DOWNLOAD_URL = ""
    settings.OIDC_TOKENS_ENCRYPTION_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def s3():
    """The rejected-files zip is the one upload that goes through boto3."""
    with patch("core.processing.folder_helper.boto3") as mock_boto3:
        yield mock_boto3


@pytest.fixture()
def drive():
    with DriveApi() as api:
        yield api


def _user():
    return factories.UserFactory(email="user@example.com")


def _make_workspace(migration_user=None, members=None):
    return factories.WorkspaceFactory(
        title="My Workspace",
        migration_user=migration_user,
        members=members or [],
    )


def _make_migration_user(email="alice@example.com"):
    return factories.UserFactory(email=email)


def _connect(user):
    user.oidc_access_token = encrypt_token("user-tok")
    user.oidc_token_expires_at = timezone.now() + timedelta(hours=1)
    user.save()


def _assert_drive_status(workspace, status):
    workspace.refresh_from_db()
    assert workspace.get_destination_status("drive") == status


def _bodies(drive, suffix):
    found = []
    for call in drive.calls:
        if call.request.method == "POST" and call.request.url.rstrip("/").endswith(
            suffix
        ):
            found.append(call.request.body)
    return found


def _json_bodies(drive, suffix):
    return [json.loads(body) for body in _bodies(drive, suffix) if body]


def _queries(drive):
    return [
        parse_qs(urlparse(call.request.url).query).get("q", [""])[0]
        for call in drive.calls
        if call.request.method == "GET"
        and "/users/" in call.request.url
        and not call.request.url.endswith("/users/me/")
    ]


def _uploads(drive):
    return [call.request for call in drive.calls if call.request.method == "PUT"]


def _root_id(workspace):
    workspace.refresh_from_db()
    return workspace.get_destination_metadata("drive")["workspace_id"]


def _errors(workspace):
    workspace.refresh_from_db()
    return [
        (error["path"], error["error"])
        for error in workspace.upload_errors
        if error.get("destination") == "drive"
    ]


def test_implements_abstract_destination():
    assert issubclass(DriveDestinationBackend, AbstractDestinationBackend)


def test_name_is_drive():
    assert DriveDestinationBackend.name == "drive"


def test_label_is_set():
    assert isinstance(DriveDestinationBackend.label, str)
    assert DriveDestinationBackend.label != ""


def test_service_account_mode_uses_the_client_credentials_token(drive, tmp_path):
    """Service-account mode asks Keycloak for a token, then calls Drive with it."""
    DriveDestinationBackend().export(_make_workspace(), _user(), str(tmp_path))

    token_call = next(
        call for call in drive.calls if call.request.url.startswith("https://oidc")
    )
    assert "grant_type=client_credentials" in token_call.request.body
    created = next(
        call
        for call in drive.calls
        if call.request.method == "POST"
        and call.request.url.rstrip("/").endswith("/items")
    )
    assert created.request.headers["Authorization"] == "Bearer service-tok"


def test_service_account_mode_creates_root_folder(drive, tmp_path):
    """export() creates a root Drive folder named after the workspace title."""
    workspace = _make_workspace()

    DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    created = _json_bodies(drive, "/items")
    assert created[0]["title"] == "My Workspace"
    assert created[0]["type"] == "folder"
    assert created[0]["id"] == _root_id(workspace)


def test_service_account_mode_stores_root_id_in_metadata(drive, tmp_path):
    """export() stores the root folder ID in destination_metadata."""
    workspace = _make_workspace()

    DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    assert _root_id(workspace)


def test_service_account_mode_creates_subfolders(drive, tmp_path):
    """export() mirrors the local folder tree as Drive subfolders."""
    (tmp_path / "docs").mkdir()
    workspace = _make_workspace()

    DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    children = _json_bodies(drive, "/children")
    assert children == [{"type": "folder", "title": "docs", "id": children[0]["id"]}]
    assert (
        f"/items/{_root_id(workspace)}/children/"
        in drive.calls[
            next(
                i
                for i, call in enumerate(drive.calls)
                if call.request.url.endswith("/children/")
            )
        ].request.url
    )


def test_service_account_mode_uploads_files(drive, tmp_path):
    """export() uploads each file via the 3-step Drive upload process."""
    (tmp_path / "report.pdf").write_bytes(b"content")
    workspace = _make_workspace()

    DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    created = _json_bodies(drive, "/children")
    assert created[0]["filename"] == "report.pdf"
    assert created[0]["size"] == 7
    item_id = created[0]["id"]
    assert any(
        call.request.method == "PUT" and call.request.url.endswith(f"/{item_id}")
        for call in drive.calls
    )
    assert any(
        call.request.url.endswith(f"/{item_id}/upload-ended/") for call in drive.calls
    )
    assert call_body(drive, item_id) == b"content"


def call_body(drive, item_id):
    return next(
        call.request.body
        for call in drive.calls
        if call.request.method == "PUT" and call.request.url.endswith(f"/{item_id}")
    )


def test_service_account_mode_shares_with_migration_user_in_drive(drive, tmp_path):
    """In service_account mode, migration_user is shared when they exist in Drive."""
    drive.users["alice@example.com"] = "user-uuid"
    workspace = _make_workspace(
        migration_user=_make_migration_user("alice@example.com")
    )

    DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    assert "alice@example.com" in _queries(drive)
    shared = _json_bodies(drive, "/accesses")
    assert shared == [{"user_id": "user-uuid", "role": "owner"}]


def test_service_account_mode_invites_migration_user_not_in_drive(drive, tmp_path):
    """In service_account mode, migration_user is invited when not registered in Drive."""
    workspace = _make_workspace(migration_user=_make_migration_user("new@example.com"))

    DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    invited = _json_bodies(drive, "/invitations")
    assert {"email": "new@example.com", "role": "owner"} in invited


def test_service_account_mode_skips_sharing_when_no_migration_user(drive, tmp_path):
    """In service_account mode, sharing is skipped when migration_user is None."""
    DriveDestinationBackend().export(
        _make_workspace(migration_user=None), _user(), str(tmp_path)
    )

    assert _queries(drive) == []
    assert _json_bodies(drive, "/accesses") == []
    assert _json_bodies(drive, "/invitations") == []


def test_service_account_mode_shares_with_workspace_members_in_drive(drive, tmp_path):
    """In service_account mode, workspace members who exist in Drive are shared."""
    drive.users["jean@example.com"] = "jean-uuid"
    drive.users["alice@example.com"] = "alice-uuid"
    workspace = _make_workspace(
        members=[{"email": "jean@example.com"}, {"email": "alice@example.com"}]
    )

    DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    assert set(_queries(drive)) == {"jean@example.com", "alice@example.com"}
    assert {body["user_id"] for body in _json_bodies(drive, "/accesses")} == {
        "jean-uuid",
        "alice-uuid",
    }


def test_service_account_mode_invites_unknown_workspace_members(drive, tmp_path):
    """In service_account mode, members not in Drive are invited by email."""
    workspace = _make_workspace(members=[{"email": "jean@example.com"}])

    DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    assert {"email": "jean@example.com", "role": "owner"} in _json_bodies(
        drive, "/invitations"
    )


def test_service_account_mode_sets_status_success(drive, tmp_path):
    """export() sets destination status to SUCCESS after successful upload."""
    workspace = _make_workspace()

    DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    _assert_drive_status(workspace, Workspace.Status.SUCCESS)


def test_service_account_mode_does_not_double_share_migration_user_who_is_also_member(
    drive, tmp_path
):
    """Migration user who is also a workspace member is shared only once."""
    drive.users["alice@example.com"] = "user-uuid"
    workspace = _make_workspace(
        migration_user=_make_migration_user("alice@example.com"),
        members=[{"email": "alice@example.com"}],
    )

    DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    assert _queries(drive).count("alice@example.com") == 1


def test_user_token_mode_calls_drive_with_the_user_token(drive, tmp_path, settings):
    """In user_token mode, Drive is called with the user's token, not a service one."""
    settings.DRIVE_AUTH_MODE = "user_token"
    user = _user()
    _connect(user)

    DriveDestinationBackend().export(_make_workspace(), user, str(tmp_path))

    assert not any(call.request.url.startswith("https://oidc") for call in drive.calls)
    created = next(
        call
        for call in drive.calls
        if call.request.method == "POST"
        and call.request.url.rstrip("/").endswith("/items")
    )
    assert created.request.headers["Authorization"] == "Bearer user-tok"


def test_user_token_mode_does_not_share_with_migration_user(drive, tmp_path, settings):
    """In user_token mode, migration_user is already owner — sharing is skipped."""
    settings.DRIVE_AUTH_MODE = "user_token"
    user = _user()
    _connect(user)
    workspace = _make_workspace(
        migration_user=_make_migration_user("alice@example.com")
    )

    DriveDestinationBackend().export(workspace, user, str(tmp_path))

    assert "alice@example.com" not in _queries(drive)


def test_user_token_mode_still_shares_with_other_members(drive, tmp_path, settings):
    """In user_token mode, other workspace members (not the migration user) are still shared."""
    settings.DRIVE_AUTH_MODE = "user_token"
    drive.users["bob@example.com"] = "bob-uuid"
    user = _user()
    _connect(user)
    workspace = _make_workspace(
        migration_user=_make_migration_user("alice@example.com"),
        members=[{"email": "alice@example.com"}, {"email": "bob@example.com"}],
    )

    DriveDestinationBackend().export(workspace, user, str(tmp_path))

    assert "alice@example.com" not in _queries(drive)
    assert "bob@example.com" in _queries(drive)


def test_user_token_mode_shares_with_members_even_without_migration_user(
    drive, tmp_path, settings
):
    """In user_token mode with no migration_user, members are still shared normally."""
    settings.DRIVE_AUTH_MODE = "user_token"
    drive.users["bob@example.com"] = "bob-uuid"
    user = _user()
    _connect(user)
    workspace = _make_workspace(
        migration_user=None, members=[{"email": "bob@example.com"}]
    )

    DriveDestinationBackend().export(workspace, user, str(tmp_path))

    assert "bob@example.com" in _queries(drive)


def test_sharing_disabled_skips_share_members(drive, tmp_path, settings):
    """When DRIVE_SHARE_MEMBERS is False, no sharing or invitation calls are made."""
    settings.DRIVE_SHARE_MEMBERS = False
    workspace = _make_workspace(
        migration_user=_make_migration_user("alice@example.com"),
        members=[{"email": "bob@example.com"}],
    )

    DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    assert _queries(drive) == []
    assert _json_bodies(drive, "/accesses") == []
    assert _json_bodies(drive, "/invitations") == []


def test_uploads_users_csv_when_workspace_has_members(drive, tmp_path):
    """export() uploads a users_list_by_migrator.csv that lists member emails only."""
    workspace = _make_workspace(
        members=[
            {"name": "Doe", "firstName": "Jean", "email": "jean@example.com"},
            {"name": "Martin", "firstName": "Alice", "email": "alice@example.com"},
        ]
    )

    DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    created = next(
        body for body in _json_bodies(drive, "/children") if body["filename"] == _CSV
    )
    assert (
        call_body(drive, created["id"]) == b"jean@example.com\r\nalice@example.com\r\n"
    )


def test_users_csv_removed_from_local_folder_after_upload(drive, tmp_path):
    """The temporary users_list_by_migrator.csv is cleaned up from the local folder after export."""
    workspace = _make_workspace(
        members=[{"name": "Doe", "firstName": "Jean", "email": "jean@example.com"}]
    )

    DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    assert not (tmp_path / _CSV).exists()


def test_no_users_csv_uploaded_when_workspace_has_no_members(drive, tmp_path):
    """No users_list_by_migrator.csv is created or uploaded when the workspace has no members."""
    DriveDestinationBackend().export(
        _make_workspace(members=[]), _user(), str(tmp_path)
    )

    assert all(
        body.get("filename") != _CSV for body in _json_bodies(drive, "/children")
    )


def test_export_records_files_drive_refused(drive, tmp_path, s3):
    """export() records the files Drive refused, and the zip is uploaded before the mail."""
    (tmp_path / "page.txt").write_bytes(b"page")
    (tmp_path / "ok.txt").write_bytes(b"ok")
    drive.reject_ended["page.txt"] = "file_type_not_allowed"

    def uploaded(*_args, **_kwargs):
        assert not mail.outbox

    s3.resource.return_value.Bucket.return_value.upload_file.side_effect = uploaded
    workspace = _make_workspace()

    DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    assert _errors(workspace) == [("page.txt", "file_type_not_allowed")]
    assert len(mail.outbox) == 1


def test_export_clears_a_previous_rejection_when_drive_accepts_everything(
    drive, tmp_path
):
    """A run without rejection still reports, to clear a previous run's archive."""
    workspace = _make_workspace()
    workspace.upload_errors = [
        {"destination": "drive", "path": "old.txt", "error": "file_type_not_allowed"}
    ]
    workspace.destination_metadata = {"drive": {"rejected_files_s3_key": "old.zip"}}
    workspace.save()

    DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    workspace.refresh_from_db()
    assert workspace.upload_errors == []
    assert "rejected_files_s3_key" not in workspace.get_destination_metadata("drive")


def test_export_keeps_reported_rejections_when_a_later_file_fails(drive, tmp_path):
    """Files refused before a fatal upload error are still reported."""
    (tmp_path / "a.txt").write_bytes(b"a")
    (tmp_path / "b.txt").write_bytes(b"b")
    drive.reject_create["a.txt"] = "file_type_not_allowed"
    drive.fatal_create.add("b.txt")
    workspace = _make_workspace()

    with pytest.raises(HTTPError):
        DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    assert _errors(workspace) == [("a.txt", "file_type_not_allowed")]


def test_export_fails_when_drive_rejects_every_file(drive, tmp_path):
    """A migration where Drive refused every file fails instead of reporting success."""
    (tmp_path / "a.txt").write_bytes(b"a")
    (tmp_path / "b.txt").write_bytes(b"b")
    drive.reject_ended["a.txt"] = "file_type_not_allowed"
    drive.reject_ended["b.txt"] = "file_type_not_allowed"
    drive.users["alice@example.com"] = "user-uuid"
    workspace = _make_workspace(migration_user=_make_migration_user())

    with pytest.raises(RuntimeError, match="All 2 file"):
        DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    assert len(_errors(workspace)) == 2
    assert _queries(drive) == []
    _assert_drive_status(workspace, Workspace.Status.NONE)


def test_export_succeeds_when_drive_rejects_some_files(drive, tmp_path):
    """One file sent is enough for the migration to succeed with its gaps listed."""
    (tmp_path / "a.txt").write_bytes(b"a")
    (tmp_path / "b.txt").write_bytes(b"b")
    drive.reject_ended["a.txt"] = "file_type_not_allowed"
    workspace = _make_workspace()

    DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    _assert_drive_status(workspace, Workspace.Status.SUCCESS)
    assert _errors(workspace) == [("a.txt", "file_type_not_allowed")]


def test_export_ignores_the_generated_users_csv_when_checking_rejections(
    drive, tmp_path
):
    """The members CSV is not a source file: sending it does not count, nor does
    its rejection in a workspace without files."""
    workspace = _make_workspace(members=[{"email": "a@example.com"}])
    (tmp_path / "a.txt").write_bytes(b"a")
    drive.reject_ended["a.txt"] = "file_type_not_allowed"

    with pytest.raises(RuntimeError):
        DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    drive.reject_ended.clear()
    drive.reject_create[_CSV] = "file_type_not_allowed"
    (tmp_path / "a.txt").unlink()
    DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    _assert_drive_status(workspace, Workspace.Status.SUCCESS)


def test_file_refused_at_upload_ended_is_skipped(drive, tmp_path):
    """Drive refusing a file's content skips it: the next files are still sent."""
    (tmp_path / "a.txt").write_bytes(b"a")
    (tmp_path / "b.txt").write_bytes(b"b")
    drive.reject_ended["a.txt"] = "file_type_not_allowed"
    workspace = _make_workspace()

    DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    assert sum(call.request.url.endswith("/upload-ended/") for call in drive.calls) == 2
    assert _errors(workspace) == [("a.txt", "file_type_not_allowed")]
    assert any(
        call.request.url.endswith("/b.txt") is False and call.request.method == "PUT"
        for call in drive.calls
    )
    _assert_drive_status(workspace, Workspace.Status.SUCCESS)


def test_file_refused_at_creation_is_skipped(drive, tmp_path):
    """A file refused at creation is listed with the id the migrator sent."""
    (tmp_path / "a.txt").write_bytes(b"a")
    (tmp_path / "b.txt").write_bytes(b"b")
    drive.reject_create["a.txt"] = "item_create_file_extension_not_allowed"
    workspace = _make_workspace()

    DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    workspace.refresh_from_db()
    refused = workspace.upload_errors[0]
    assert refused["path"] == "a.txt"
    assert refused["error"] == "item_create_file_extension_not_allowed"
    sent = next(
        body
        for body in _json_bodies(drive, "/children")
        if body.get("filename") == "a.txt"
    )
    assert refused["item_id"] == sent["id"]
    uploaded = [
        call.request.url for call in drive.calls if call.request.method == "PUT"
    ]
    assert len(uploaded) == 1
    assert uploaded[0].endswith(drive.item_by_filename("b.txt")["id"])


@pytest.mark.parametrize("probe", ["ok", "missing"])
def test_file_blocked_upstream_at_creation_is_skipped(drive, probe, tmp_path):
    """A file whose creation is blocked in front of Drive is skipped once a
    payload-less request to its folder gets an answer from Drive, even a 404."""
    (tmp_path / "a.txt").write_bytes(b"a")
    (tmp_path / "b.txt").write_bytes(b"b")
    drive.block_create.add("a.txt")
    drive.probe = probe
    workspace = _make_workspace()

    DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    assert _errors(workspace) == [("a.txt", "blocked_upstream")]
    assert len(_uploads(drive)) == 1


@pytest.mark.parametrize("probe", ["blocked", "timeout", "connection"])
def test_upstream_block_of_every_request_stops_the_upload(drive, probe, tmp_path):
    """A block the payload-less probe also gets is not the file's."""
    (tmp_path / "a.txt").write_bytes(b"a")
    (tmp_path / "b.txt").write_bytes(b"b")
    drive.block_create.add("a.txt")
    drive.probe = probe
    workspace = _make_workspace()

    with pytest.raises((HTTPError, Timeout, RequestsConnectionError)):
        DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    creates = [
        body for body in _json_bodies(drive, "/children") if body.get("type") == "file"
    ]
    assert len(creates) == 1
    assert _errors(workspace) == []


def test_upstream_block_at_upload_ended_stops_the_upload(drive, tmp_path):
    """upload-ended carries no file name: a block there is not the file's."""
    (tmp_path / "a.txt").write_bytes(b"a")
    drive.block_ended = True
    workspace = _make_workspace()

    with pytest.raises(HTTPError):
        DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    assert _errors(workspace) == []


def test_other_drive_error_still_stops_the_upload(drive, tmp_path):
    """An error that is not a refusal of the file still stops the migration."""
    (tmp_path / "a.txt").write_bytes(b"a")
    (tmp_path / "b.txt").write_bytes(b"b")
    drive.reject_ended["a.txt"] = "item_upload_type_unavailable"
    workspace = _make_workspace()

    with pytest.raises(HTTPError):
        DriveDestinationBackend().export(workspace, _user(), str(tmp_path))

    assert sum("upload-ended" in call.request.url for call in drive.calls) == 1
    assert _errors(workspace) == []


def test_export_logs_rejected_files_once_without_paths(drive, tmp_path, caplog):
    """Rejections are summed up in a single ERROR, with item ids and codes only."""
    (tmp_path / "a.txt").write_bytes(b"a")
    (tmp_path / "b.txt").write_bytes(b"b")
    drive.reject_ended["a.txt"] = "file_type_not_allowed"

    with caplog.at_level(logging.ERROR):
        DriveDestinationBackend().export(_make_workspace(), _user(), str(tmp_path))

    errors = [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.ERROR
    ]
    summary = next(message for message in errors if "rejected on upload" in message)
    assert "a.txt" not in summary
    assert "file_type_not_allowed" in summary


def test_export_logs_nothing_at_error_without_rejection(drive, tmp_path, caplog):
    """A run where Drive accepted every file produces no rejection event."""
    (tmp_path / "report.pdf").write_bytes(b"content")

    with caplog.at_level(logging.ERROR, logger="core.destinations.drive.backend"):
        DriveDestinationBackend().export(_make_workspace(), _user(), str(tmp_path))

    assert not [
        record
        for record in caplog.records
        if record.levelno >= logging.ERROR
        and "rejected on upload" in record.getMessage()
    ]


def test_export_logs_each_file_before_creating_it(drive, tmp_path, caplog):
    """Each upload is logged with its item id and size, before Drive is called."""
    (tmp_path / "report.pdf").write_bytes(b"content")
    drive.fatal_create.add("report.pdf")

    with caplog.at_level(logging.INFO), pytest.raises(HTTPError):
        DriveDestinationBackend().export(_make_workspace(), _user(), str(tmp_path))

    message = next(
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Uploading file")
    )
    sent = next(
        body
        for body in _json_bodies(drive, "/children")
        if body.get("filename") == "report.pdf"
    )
    assert sent["id"] in message
    assert "7" in message


def test_user_token_mode_sets_status_success(drive, tmp_path, settings):
    """In user_token mode, export() sets destination status to SUCCESS."""
    settings.DRIVE_AUTH_MODE = "user_token"
    user = _user()
    _connect(user)
    workspace = _make_workspace()

    DriveDestinationBackend().export(workspace, user, str(tmp_path))

    _assert_drive_status(workspace, Workspace.Status.SUCCESS)


def test_export_sends_drive_ready_mail(drive, tmp_path):
    """export() sends a 'drive_ready' migration mail to the user after upload."""
    workspace = _make_workspace()
    user = _user()

    DriveDestinationBackend().export(workspace, user, str(tmp_path))

    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == [user.email]
    assert workspace.title in sent.subject
    assert "Votre espace LaSuite Fichiers est prêt" in sent.body


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        (
            "item_create_file_extension_not_allowed",
            "Extension de fichier non autorisée",
        ),
        ("file_type_not_allowed", "Type de fichier non autorisé"),
        ("file_size_exceeded", "Fichier trop volumineux"),
        ("file_size_mismatch", "Fichier corrompu pendant le transfert"),
        ("blocked_upstream", "Fichier bloqué en amont de Drive (ex. pare-feu)"),
        ("new_code", "Fichier refusé par La Suite Drive"),
    ],
)
def test_describe_rejection_explains_each_drive_code(code, reason):
    """Each known Drive code gets its own reason, an unknown one the default."""
    assert DriveDestinationBackend().describe_rejection(code) == reason


@pytest.mark.parametrize("rejection", list(FileRejection))
def test_every_file_rejection_has_a_reason(rejection):
    """A new FileRejection can't reach the CSV without its readable reason."""
    assert (
        DriveDestinationBackend().describe_rejection(rejection)
        != "Fichier refusé par La Suite Drive"
    )


def test_account_policy_is_none_in_service_account_mode(settings):
    """The service account creates the items: no user account is involved."""
    settings.DRIVE_AUTH_MODE = "service_account"
    settings.DRIVE_ACCOUNT_POLICY = "user_created"

    assert DriveDestinationBackend().get_account_policy() is AccountPolicy.NONE


@pytest.mark.parametrize(
    "policy", [AccountPolicy.USER_CREATED, AccountPolicy.AUTO_CREATED]
)
def test_account_policy_follows_the_setting_in_user_token_mode(settings, policy):
    """In user_token mode, DRIVE_ACCOUNT_POLICY says who creates the account."""
    settings.DRIVE_AUTH_MODE = "user_token"
    settings.DRIVE_ACCOUNT_POLICY = policy.value

    assert DriveDestinationBackend().get_account_policy() is policy


def test_invalid_account_policy_falls_back_to_user_created(settings):
    """A mistyped setting must not break every page listing the destinations."""
    settings.DRIVE_AUTH_MODE = "user_token"
    settings.DRIVE_ACCOUNT_POLICY = "user-created"

    assert DriveDestinationBackend().get_account_policy() is AccountPolicy.USER_CREATED


def test_account_creation_url_is_the_drive_frontend(settings):
    """Logging in to Drive once creates the account."""
    settings.DRIVE_AUTH_MODE = "user_token"
    settings.DRIVE_ACCOUNT_POLICY = "user_created"
    settings.DRIVE_FRONTEND_URL = "https://fichiers.example.com"

    assert (
        DriveDestinationBackend().get_account_creation_url()
        == "https://fichiers.example.com"
    )


@pytest.mark.parametrize(
    "auth_mode, policy",
    [("user_token", "auto_created"), ("service_account", "user_created")],
)
def test_no_account_creation_url_when_the_user_creates_no_account(
    settings, auth_mode, policy
):
    """Only a destination the user must create their account on links to it."""
    settings.DRIVE_AUTH_MODE = auth_mode
    settings.DRIVE_ACCOUNT_POLICY = policy
    settings.DRIVE_FRONTEND_URL = "https://fichiers.example.com"

    assert DriveDestinationBackend().get_account_creation_url() is None


@pytest.mark.parametrize("status, expected", [(200, True), (401, False)])
def test_user_account_exists_asks_drive_with_the_user_token(drive, status, expected):
    """The account exists when Drive knows the holder of the user's token."""
    user = _user()
    _connect(user)
    drive.me_status = status

    assert DriveDestinationBackend().user_account_exists(user) is expected
    assert any(call.request.url.endswith("/users/me/") for call in drive.calls)
