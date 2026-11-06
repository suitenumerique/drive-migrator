"""Tests for DriveDestinationBackend, exported file by file."""

# pylint: disable=redefined-outer-name  # pytest fixtures intentionally shadow outer names

import os
from unittest.mock import ANY, MagicMock, patch

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import HTTPError, Timeout

from core.backends.destination import (
    AbstractDestinationBackend,
    AccountPolicy,
    FileExportStatus,
    RejectedFile,
)
from core.destinations.drive.backend import DriveDestinationBackend
from core.destinations.drive.drive_backend import FileRejection, RecoveredItemUploaded
from core.factories import UserFactory
from core.models import Workspace

pytestmark = pytest.mark.django_db

CSV_NAME = "users_list_by_migrator.csv"


def test_implements_abstract_destination():
    assert issubclass(DriveDestinationBackend, AbstractDestinationBackend)


def test_name_is_drive():
    assert DriveDestinationBackend.name == "drive"


def test_label_is_set():
    assert isinstance(DriveDestinationBackend.label, str)
    assert DriveDestinationBackend.label != ""


def _drive_refusal(code, status_code=400):
    """Build the HTTPError Drive raises when it refuses a file."""
    response = MagicMock(status_code=status_code)
    response.json.return_value = {
        "type": "validation_error",
        "errors": [{"code": code, "detail": "Refused.", "attr": None}],
    }
    return HTTPError(f"{status_code} Client Error", response=response)


def _drive_results(run):
    return {row.path: row.results.get("drive") for row in run.files.all()}


# ---------------------------------------------------------------------------
# Drive client
# ---------------------------------------------------------------------------


def test_service_account_mode_creates_one_client_per_run(
    drive_client, make_run, migrate
):
    """In service_account mode, a single client (and token) serves every file."""
    with patch("core.destinations.drive.backend.DriveServiceAccountBackend") as cls:
        cls.return_value = drive_client
        migrate(make_run(paths=["a.txt", "b.txt"]))

    cls.assert_called_once_with()


def test_user_token_mode_uses_the_user_token_client(
    settings, drive_client, make_run, migrate, user
):
    """In user_token mode, the client acts with the migration user's token."""
    settings.DRIVE_AUTH_MODE = "user_token"
    with patch("core.destinations.drive.backend.DriveUserTokenBackend") as cls:
        cls.return_value = drive_client
        migrate(make_run())

    cls.assert_called_once_with(user)


# ---------------------------------------------------------------------------
# open_export(): root folder and every planned folder
# ---------------------------------------------------------------------------


def test_open_creates_the_root_folder_and_keeps_its_id(drive_client, make_run, migrate):
    """The root folder is named after the workspace, its id kept in its metadata."""
    run = make_run(paths=[])

    migrate(run)

    drive_client.create_folder.assert_called_once_with("My Workspace")
    run.workspace.refresh_from_db()
    assert run.workspace.get_destination_metadata("drive") == {
        "workspace_id": "root-uuid"
    }


def test_open_creates_every_planned_folder_under_its_parent(
    drive_client, make_run, migrate
):
    """Folders are created parents first, empty ones included, and kept on the run."""
    run = make_run(paths=[os.path.join("docs", "sub", "a.txt")], dirs=["empty"])

    migrate(run)

    assert drive_client.create_subfolder.call_args_list == [
        (("empty",), {"parent_id": "root-uuid"}),
        (("docs",), {"parent_id": "root-uuid"}),
        (("sub",), {"parent_id": "folder-docs"}),
    ]
    run.refresh_from_db()
    assert run.destinations_state["drive"]["folder_ids"] == {
        "empty": "folder-empty",
        "docs": "folder-docs",
        os.path.join("docs", "sub"): "folder-sub",
    }


def test_a_root_folder_failure_disables_drive(drive_client, make_run, migrate):
    """Without its root folder, Drive gets no file at all."""
    drive_client.create_folder.side_effect = RuntimeError("Drive down")

    failures = migrate(make_run())

    assert list(failures) == ["drive"]
    drive_client.create_file_item.assert_not_called()


# ---------------------------------------------------------------------------
# put_file(): the three-step upload of one file
# ---------------------------------------------------------------------------


def test_put_uploads_the_file_in_its_folder(drive_client, make_run, migrate, tmp_path):
    """Each file is created in its Drive folder, sent to S3, then completed."""
    path = os.path.join("docs", "report.pdf")
    run = make_run(paths=[path], size=7)

    migrate(run)

    item_id = drive_client.create_file_item.call_args.kwargs["item_id"]
    drive_client.create_file_item.assert_called_once_with(
        "report.pdf", parent_id="folder-docs", size=7, item_id=item_id
    )
    drive_client.upload_to_s3.assert_called_once_with(
        "https://s3.example.com/report.pdf", str(tmp_path / path)
    )
    drive_client.notify_upload_ended.assert_called_once_with(item_id)
    assert _drive_results(run) == {
        path: {"status": FileExportStatus.SENT, "item_id": item_id}
    }


def test_item_ids_are_derived_from_the_rows(drive_client, make_run, migrate, tmp_path):
    """A replayed file gets the same id again; two files never share one."""
    run = make_run(paths=["a.txt", "b.txt"])

    migrate(run)
    first_ids = [
        call.kwargs["item_id"] for call in drive_client.create_file_item.call_args_list
    ]
    drive_client.create_file_item.reset_mock()
    destination = DriveDestinationBackend()
    for migration_file in run.files.all():
        destination.put_file(
            UserFactory(), run, migration_file, str(tmp_path / migration_file.path)
        )

    assert len(set(first_ids)) == 2
    assert [
        call.kwargs["item_id"] for call in drive_client.create_file_item.call_args_list
    ] == first_ids


def test_put_keeps_the_id_drive_gives_back(drive_client, make_run, migrate):
    """Drive may replace a recovered pending item: its new id is the one kept."""
    drive_client.create_file_item.side_effect = lambda title, **kwargs: {
        "id": "drive-id",
        "policy": "https://s3.example.com/x",
    }
    run = make_run()

    migrate(run)

    drive_client.notify_upload_ended.assert_called_once_with("drive-id")
    assert _drive_results(run)["report.pdf"]["item_id"] == "drive-id"


def test_a_file_sent_again_is_kept_as_sent(drive_client, make_run, migrate):
    """A file task run again, e.g. by the watchdog, finds its item uploaded
    already: the file is sent, and Drive keeps receiving the next ones."""

    def recovered(_title, **kwargs):
        raise RecoveredItemUploaded({"id": kwargs["item_id"], "upload_state": "ready"})

    drive_client.create_file_item.side_effect = recovered
    run = make_run()

    assert migrate(run) == {}

    drive_client.upload_to_s3.assert_not_called()
    assert _drive_results(run)["report.pdf"] == {
        "status": FileExportStatus.SENT,
        "item_id": ANY,
    }


def test_file_refused_at_upload_ended_is_rejected(drive_client, make_run, migrate):
    """Drive refusing a file's content records it: the next files are still sent."""
    drive_client.notify_upload_ended.side_effect = [
        _drive_refusal("file_type_not_allowed"),
        None,
    ]
    run = make_run(paths=["a.txt", "b.txt"])

    assert migrate(run) == {}

    results = _drive_results(run)
    assert results["a.txt"] == {
        "status": FileExportStatus.REJECTED,
        "item_id": ANY,
        "error": "file_type_not_allowed",
    }
    assert results["b.txt"]["status"] == FileExportStatus.SENT


def test_file_refused_at_creation_keeps_the_client_id(drive_client, make_run, migrate):
    """A file refused before Drive gave an id is listed with the one sent."""
    drive_client.create_file_item.side_effect = _drive_refusal(
        "item_create_file_extension_not_allowed"
    )
    run = make_run()

    migrate(run)

    sent_id = drive_client.create_file_item.call_args.kwargs["item_id"]
    assert _drive_results(run)["report.pdf"] == {
        "status": FileExportStatus.REJECTED,
        "item_id": sent_id,
        "error": "item_create_file_extension_not_allowed",
    }
    drive_client.upload_to_s3.assert_not_called()


def _upstream_block():
    """Build the HTTPError something in front of Drive (e.g. a WAF) raises."""
    response = MagicMock(status_code=403, text="<html>Request unsuccessful.</html>")
    response.json.side_effect = ValueError
    return HTTPError("403 Client Error", response=response)


@pytest.mark.parametrize(
    "probed_folder", [{"id": "root-uuid"}, None], ids=["found", "not-found"]
)
def test_file_blocked_upstream_at_creation_is_rejected(
    drive_client, make_run, migrate, probed_folder
):
    """A file whose creation is blocked in front of Drive is refused once a
    payload-less request to its folder gets an answer from Drive, even a 404."""
    drive_client.get_item.return_value = probed_folder
    drive_client.create_file_item.side_effect = _upstream_block()
    run = make_run()

    migrate(run)

    drive_client.get_item.assert_called_once_with("root-uuid")
    sent_id = drive_client.create_file_item.call_args.kwargs["item_id"]
    assert _drive_results(run)["report.pdf"] == {
        "status": FileExportStatus.REJECTED,
        "item_id": sent_id,
        "error": "blocked_upstream",
    }


@pytest.mark.parametrize(
    "probe_error",
    [_upstream_block(), Timeout(), RequestsConnectionError()],
    ids=["blocked", "timeout", "connection-error"],
)
def test_upstream_block_of_every_request_fails_the_file(
    drive_client, make_run, migrate, probe_error
):
    """A block the payload-less probe also gets (rate limit, IP reputation...)
    is not the file's: it fails like any other error, not as a refusal."""
    drive_client.create_file_item.side_effect = _upstream_block()
    drive_client.get_item.side_effect = probe_error
    run = make_run()

    migrate(run)

    assert _drive_results(run)["report.pdf"]["status"] == FileExportStatus.FAILED


def test_upstream_block_at_upload_ended_fails_the_file(drive_client, make_run, migrate):
    """upload-ended carries no file name: a block there is not the file's."""
    drive_client.notify_upload_ended.side_effect = _upstream_block()
    run = make_run()

    migrate(run)

    drive_client.get_item.assert_not_called()
    assert _drive_results(run)["report.pdf"]["status"] == FileExportStatus.FAILED


@pytest.mark.parametrize(
    "error",
    [_drive_refusal("item_upload_type_unavailable"), Timeout("read timed out")],
    ids=["other_drive_error", "timeout"],
)
def test_other_errors_fail_the_file_only(drive_client, make_run, migrate, error):
    """An error that is not a refusal of the file fails it, not the whole export."""
    drive_client.notify_upload_ended.side_effect = [error, None]
    run = make_run(paths=["a.txt", "b.txt"])

    assert migrate(run) == {}

    results = _drive_results(run)
    assert results["a.txt"] == {
        "status": FileExportStatus.FAILED,
        "item_id": ANY,
        "error": str(error),
    }
    assert results["b.txt"]["status"] == FileExportStatus.SENT


def test_a_failed_file_is_logged_without_the_error_message(
    drive_client, make_run, migrate
):
    """The warning names the error type and status only: the message may hold
    the URL, hence the file name."""
    error = _drive_refusal("server_error", 500)
    drive_client.notify_upload_ended.side_effect = error

    with patch("core.destinations.drive.backend.logger") as mock_logger:
        migrate(make_run())

    item_id = drive_client.create_file_item.call_args.kwargs["item_id"]
    mock_logger.warning.assert_called_once_with(
        "Upload of file %s failed: %s %s", item_id, "HTTPError", 500
    )


@pytest.mark.parametrize(
    "error",
    [_drive_refusal("not_authenticated", 401), RuntimeError("cannot refresh token")],
    ids=["unauthorized", "token_refresh"],
)
def test_authentication_errors_disable_drive(drive_client, make_run, migrate, error):
    """An unusable token would fail every file: Drive gets no more of them."""
    drive_client.create_file_item.side_effect = error
    run = make_run(paths=["a.txt", "b.txt"])

    failures = migrate(run)

    assert list(failures) == ["drive"]
    assert drive_client.create_file_item.call_count == 1


def test_put_logs_each_file_before_creating_it(drive_client, make_run, migrate):
    """Each upload is logged with its item id and size, before Drive is called."""
    with patch("core.destinations.drive.backend.logger") as mock_logger:
        migrate(make_run(size=7))

    item_id = drive_client.create_file_item.call_args.kwargs["item_id"]
    mock_logger.info.assert_any_call("Uploading file %s (%s bytes)", item_id, 7)


# ---------------------------------------------------------------------------
# close_export(): members listing, sharing, status and mail
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("drive_client")
def test_close_sets_status_success_and_sends_the_mail(
    make_run, migrate, user, mailoutbox, settings
):
    """A completed export is a success, announced by a drive_ready mail."""
    settings.APP_EMAIL_FORCE_TO = None
    run = make_run()

    assert migrate(run) == {}

    run.workspace.refresh_from_db()
    assert run.workspace.get_destination_status("drive") == Workspace.Status.SUCCESS
    assert [(mail.to, mail.subject) for mail in mailoutbox] == [
        ([user.email], "Votre espace My Workspace est prêt sur La Suite Drive !")
    ]


def test_close_uploads_the_members_listing(drive_client, make_run, migrate):
    """The shared members are listed at the root, one email per line, from a
    temporary file that is then removed."""
    run = make_run(
        paths=[],
        members=[
            {"name": "Doe", "firstName": "Jean", "email": "jean@example.com"},
            {"name": "Martin", "firstName": "Alice", "email": "alice@example.com"},
        ],
    )
    sent_contents = []

    def upload_to_s3(_policy, path):
        with open(path, encoding="utf-8", newline="") as fh:
            sent_contents.append((os.path.basename(path), fh.read(), path))

    drive_client.upload_to_s3.side_effect = upload_to_s3

    migrate(run)

    drive_client.create_file_item.assert_called_once_with(
        CSV_NAME, parent_id="root-uuid", size=ANY, item_id=ANY
    )
    assert len(sent_contents) == 1
    name, content, path = sent_contents[0]
    assert name == CSV_NAME
    assert content == "jean@example.com\r\nalice@example.com\r\n"
    assert not os.path.exists(path)


def test_close_uploads_no_listing_without_members(drive_client, make_run, migrate):
    """No CSV is created when the workspace has no members."""
    migrate(make_run(members=[]))

    titles = [call.args[0] for call in drive_client.create_file_item.call_args_list]
    assert CSV_NAME not in titles


def _sharing_run(make_run, migration_email=None, members=()):
    migration_user = UserFactory(email=migration_email) if migration_email else None
    return make_run(
        paths=[],
        migration_user=migration_user,
        members=[{"email": email} for email in members],
    )


def _queried_emails(drive_client):
    return [call.args[0] for call in drive_client.find_user_by_email.call_args_list]


def test_service_account_mode_shares_with_the_migration_user_in_drive(
    drive_client, make_run, migrate
):
    """The migration user is shared when they exist in Drive."""
    drive_client.find_user_by_email.return_value = {"id": "user-uuid"}

    migrate(_sharing_run(make_run, "alice@example.com"))

    drive_client.find_user_by_email.assert_any_call("alice@example.com")
    drive_client.share_with_user.assert_any_call("root-uuid", "user-uuid")


def test_service_account_mode_invites_users_not_in_drive(
    drive_client, make_run, migrate
):
    """The migration user and members unknown to Drive are invited by email."""
    drive_client.find_user_by_email.return_value = None

    migrate(_sharing_run(make_run, "new@example.com", ["jean@example.com"]))

    drive_client.invite_by_email.assert_any_call("root-uuid", "new@example.com")
    drive_client.invite_by_email.assert_any_call("root-uuid", "jean@example.com")


def test_service_account_mode_skips_sharing_without_anyone_to_share_with(
    drive_client, make_run, migrate
):
    """Without migration user nor members, no sharing call is made."""
    migrate(_sharing_run(make_run))

    drive_client.find_user_by_email.assert_not_called()
    drive_client.share_with_user.assert_not_called()
    drive_client.invite_by_email.assert_not_called()


def test_service_account_mode_shares_with_members_in_drive(
    drive_client, make_run, migrate
):
    """Workspace members who exist in Drive are shared."""
    drive_client.find_user_by_email.return_value = {"id": "user-uuid"}

    migrate(_sharing_run(make_run, members=["jean@example.com", "alice@example.com"]))

    assert sorted(_queried_emails(drive_client)) == [
        "alice@example.com",
        "jean@example.com",
    ]
    assert drive_client.share_with_user.call_count == 2


def test_migration_user_who_is_also_member_is_shared_once(
    drive_client, make_run, migrate
):
    """Emails are deduplicated across the migration user and the members."""
    drive_client.find_user_by_email.return_value = {"id": "user-uuid"}

    migrate(_sharing_run(make_run, "alice@example.com", ["alice@example.com"]))

    assert _queried_emails(drive_client) == ["alice@example.com"]


def test_user_token_mode_does_not_share_with_the_migration_user(
    settings, drive_client, make_run, migrate
):
    """The token holder already owns the folder: only the other members are shared."""
    settings.DRIVE_AUTH_MODE = "user_token"
    drive_client.find_user_by_email.return_value = {"id": "other-user-uuid"}
    run = _sharing_run(make_run, "alice@example.com", ["alice@example.com", "bob@x.fr"])

    with patch("core.destinations.drive.backend.DriveUserTokenBackend") as cls:
        cls.return_value = drive_client
        migrate(run)

    assert _queried_emails(drive_client) == ["bob@x.fr"]


def test_user_token_mode_shares_with_members_without_migration_user(
    settings, drive_client, make_run, migrate
):
    """Without a migration user, the members are still shared in user_token mode."""
    settings.DRIVE_AUTH_MODE = "user_token"
    drive_client.find_user_by_email.return_value = {"id": "user-uuid"}
    run = _sharing_run(make_run, members=["bob@example.com"])

    with patch("core.destinations.drive.backend.DriveUserTokenBackend") as cls:
        cls.return_value = drive_client
        migrate(run)

    assert _queried_emails(drive_client) == ["bob@example.com"]


def test_user_token_mode_sets_status_success(settings, drive_client, make_run, migrate):
    """The export ends in SUCCESS whatever the auth mode."""
    settings.DRIVE_AUTH_MODE = "user_token"
    run = make_run()

    with patch("core.destinations.drive.backend.DriveUserTokenBackend") as cls:
        cls.return_value = drive_client
        assert migrate(run) == {}

    run.workspace.refresh_from_db()
    assert run.workspace.get_destination_status("drive") == Workspace.Status.SUCCESS


def test_sharing_disabled_skips_sharing(settings, drive_client, make_run, migrate):
    """When DRIVE_SHARE_MEMBERS is False, no sharing or invitation is made."""
    settings.DRIVE_SHARE_MEMBERS = False

    migrate(_sharing_run(make_run, "alice@example.com", ["bob@example.com"]))

    drive_client.find_user_by_email.assert_not_called()
    drive_client.share_with_user.assert_not_called()
    drive_client.invite_by_email.assert_not_called()


# ---------------------------------------------------------------------------
# Files Drive refused or failed to receive: archive, all-lost rule
# ---------------------------------------------------------------------------


def _refuse(drive_client, *titles):
    """Make Drive refuse the content of the files with the given titles."""

    def notify(item_id):
        if any(item_id == ids[title] for title in titles):
            raise _drive_refusal("file_type_not_allowed")

    ids = {}

    def create(title, item_id, **_kwargs):
        ids[title] = item_id
        return {"id": item_id, "policy": f"https://s3.example.com/{title}"}

    drive_client.create_file_item.side_effect = create
    drive_client.notify_upload_ended.side_effect = notify
    return ids


def _recorded(rejected_files_archive):
    """Files the last export handed to the rejected files archive."""
    _, rejected = rejected_files_archive.return_value.build.call_args.args
    return rejected


def test_rejected_files_are_handed_to_the_archive(
    drive_client, make_run, migrate, rejected_files_archive
):
    """The files Drive refused are handed to the archive of the run."""
    ids = _refuse(drive_client, "a.txt")
    run = make_run(paths=["a.txt", "b.txt"])
    destination = DriveDestinationBackend()

    migrate(run, destination=destination)

    (workspace, archived_for), _ = rejected_files_archive.call_args
    assert (workspace.pk, archived_for) == (run.workspace.pk, destination)
    rejected_files_archive.return_value.build.assert_called_once_with(
        run,
        [RejectedFile("a.txt", "file_type_not_allowed", ids["a.txt"])],
    )


def test_failed_files_are_handed_to_the_archive(
    drive_client, make_run, migrate, rejected_files_archive
):
    """Files lost to an error are archived too, under a code of their own:
    the error message may hold URLs."""
    drive_client.notify_upload_ended.side_effect = [Timeout("read timed out"), None]

    migrate(make_run(paths=["a.txt", "b.txt"]))

    assert _recorded(rejected_files_archive) == [
        RejectedFile("a.txt", "upload_failed", ANY)
    ]


@pytest.mark.usefixtures("drive_client")
def test_the_archive_is_built_even_when_nothing_is_rejected(
    make_run, migrate, rejected_files_archive
):
    """A run without rejection still reports, to clear a previous run's errors."""
    migrate(make_run())

    assert _recorded(rejected_files_archive) == []


@pytest.mark.usefixtures("drive_client")
def test_the_archive_is_built_before_the_mail(
    make_run, migrate, rejected_files_archive, mailoutbox
):
    """The archive exists when the mail, which links it, is sent."""
    mails_at_build = []
    rejected_files_archive.return_value.build.side_effect = (
        lambda *args: mails_at_build.append(len(mailoutbox))
    )

    migrate(make_run())

    assert mails_at_build == [0]
    assert len(mailoutbox) == 1


def test_rejected_files_are_archived_when_drive_gets_disabled(
    drive_client, make_run, migrate, rejected_files_archive
):
    """Files refused before Drive became unusable are still archived
    (abort_export)."""
    ids = _refuse(drive_client, "a.txt")
    upload = drive_client.upload_to_s3

    def fail_on_second(*_args):
        if upload.call_count == 2:
            raise _drive_refusal("not_authenticated", 401)

    upload.side_effect = fail_on_second
    run = make_run(paths=["a.txt", "b.txt"])

    assert list(migrate(run)) == ["drive"]

    assert _recorded(rejected_files_archive) == [
        RejectedFile("a.txt", "file_type_not_allowed", ids["a.txt"])
    ]


def test_drive_fails_when_it_rejects_every_file(
    drive_client, make_run, migrate, rejected_files_archive
):
    """An export where Drive refused every file fails instead of reporting success."""
    _refuse(drive_client, "a.txt", "b.txt")
    run = make_run(paths=["a.txt", "b.txt"], migration_user=UserFactory())

    failures = migrate(run)

    assert "All 2 file(s)" in str(failures["drive"])
    assert len(_recorded(rejected_files_archive)) == 2
    drive_client.find_user_by_email.assert_not_called()
    run.workspace.refresh_from_db()
    assert run.workspace.get_destination_status("drive") != Workspace.Status.SUCCESS


def test_a_drive_failing_after_its_archive_does_not_build_it_again(
    drive_client, make_run, migrate, rejected_files_archive
):
    """The abort that follows a failed close keeps the archive the close built."""
    _refuse(drive_client, "a.txt", "b.txt")

    migrate(make_run(paths=["a.txt", "b.txt"]))

    rejected_files_archive.return_value.build.assert_called_once()


def test_a_drive_failing_before_its_archive_still_builds_it(
    drive_client, make_run, migrate, rejected_files_archive
):
    """A close failing before the archive leaves it to the abort."""
    ids = _refuse(drive_client, "a.txt")
    create = drive_client.create_file_item.side_effect

    def fail_on_listing(title, item_id, **kwargs):
        if title == CSV_NAME:
            raise _drive_refusal("not_authenticated", 401)
        return create(title, item_id, **kwargs)

    drive_client.create_file_item.side_effect = fail_on_listing
    run = make_run(paths=["a.txt", "b.txt"], members=[{"email": "jean@example.com"}])

    assert "drive" in migrate(run)

    rejected_files_archive.return_value.build.assert_called_once()
    assert _recorded(rejected_files_archive) == [
        RejectedFile("a.txt", "file_type_not_allowed", ids["a.txt"])
    ]


def test_drive_fails_when_every_file_failed(drive_client, make_run, migrate, settings):
    """Files lost to errors count like refused ones: nothing reached Drive."""
    settings.MIGRATION_DESTINATION_MAX_CONSECUTIVE_FAILURES = 10
    drive_client.notify_upload_ended.side_effect = Timeout("read timed out")

    failures = migrate(make_run(paths=["a.txt", "b.txt"]))

    assert "All 2 file(s)" in str(failures["drive"])


def test_drive_succeeds_when_it_rejects_some_files(drive_client, make_run, migrate):
    """One file sent is enough for the export to succeed with its gaps listed."""
    _refuse(drive_client, "a.txt")
    run = make_run(paths=["a.txt", "b.txt"])

    assert migrate(run) == {}

    run.workspace.refresh_from_db()
    assert run.workspace.get_destination_status("drive") == Workspace.Status.SUCCESS


def test_the_members_listing_does_not_count_as_a_sent_file(
    drive_client, make_run, migrate
):
    """The CSV is not a source file: sending it does not save an all-refused export."""
    _refuse(drive_client, "a.txt")
    run = make_run(paths=["a.txt"], members=[{"email": "jean@example.com"}])

    assert "drive" in migrate(run)


def test_a_workspace_without_files_succeeds_even_if_its_listing_is_refused(
    drive_client, make_run, migrate, rejected_files_archive
):
    """With no source file, a refused CSV alone does not fail the export; the
    archive leaves it out, being the migrator's file."""
    ids = _refuse(drive_client, CSV_NAME)
    run = make_run(paths=[], members=[{"email": "jean@example.com"}])

    assert migrate(run) == {}

    assert _recorded(rejected_files_archive) == [
        RejectedFile(CSV_NAME, "file_type_not_allowed", ids[CSV_NAME])
    ]


def test_lost_files_are_logged_once_without_paths(drive_client, make_run, migrate):
    """Refused and failed files are summed up in a single ERROR, with item ids
    and codes only: error messages may hold URLs, hence file names."""
    ids = _refuse(drive_client, "a.txt")
    refuse_a = drive_client.notify_upload_ended.side_effect

    def notify(item_id):
        if item_id == ids.get("b.txt"):
            raise Timeout(f"timed out for url: https://drive/{item_id}/b.txt")
        refuse_a(item_id)

    drive_client.notify_upload_ended.side_effect = notify

    with patch("core.destinations.drive.backend.logger") as mock_logger:
        migrate(make_run(paths=["a.txt", "b.txt", "c.txt"]))

    mock_logger.error.assert_called_once_with(
        "%s file(s) not received by Drive: %s",
        2,
        [(ids["a.txt"], "file_type_not_allowed"), (ids["b.txt"], "upload_failed")],
    )


@pytest.mark.usefixtures("drive_client")
def test_nothing_is_logged_at_error_without_rejection(make_run, migrate):
    """An export where Drive accepted every file produces no Sentry event."""
    with patch("core.destinations.drive.backend.logger") as mock_logger:
        migrate(make_run())

    mock_logger.error.assert_not_called()


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
        ("upload_failed", "Fichier non reçu par La Suite Drive (erreur d'envoi)"),
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


# ---------------------------------------------------------------------------
# User account policy
# ---------------------------------------------------------------------------


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


@pytest.mark.parametrize("me, expected", [({"id": "alice-uuid"}, True), (None, False)])
@patch("core.destinations.drive.backend.DriveUserTokenBackend")
def test_user_account_exists_asks_drive_with_the_user_token(mock_cls, me, expected):
    """The account exists when Drive knows the holder of the user's token."""
    user = MagicMock()
    mock_cls.return_value.get_me.return_value = me

    assert DriveDestinationBackend().user_account_exists(user) is expected
    mock_cls.assert_called_once_with(user)
