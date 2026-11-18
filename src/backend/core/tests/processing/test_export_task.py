"""Tests for the generic export Celery task."""

# pylint: disable=redefined-outer-name  # pytest fixtures intentionally shadow outer names
# pylint: disable=no-member  # django.core.mail.outbox is injected by the test runner

import logging
import os
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.core import mail
from django.utils import timezone

import jwt
import pytest
import responses
from cryptography.fernet import Fernet
from django_celery_results.models import TaskResult

from core import factories
from core.backends.destination import DestinationRegistry
from core.backends.source import SourceFolder
from core.models import ExtraTaskInfo, User, Workspace
from core.processing.tasks import (
    cleanup_workspace_dir,
    create_task_result_on_publish,
    debug_folder,
    export,
    list_work_dir,
    list_workspace_dir,
)
from core.processing.tasks import (
    task_failure as task_failure_handler,
)
from core.processing.tasks import (
    task_success as task_success_handler,
)
from core.sources.resana.token_manager import ResanaTokenExpired, ResanaTokenManager

pytestmark = pytest.mark.django_db

_ARCHIVE = "core.destinations.archive.backend.ArchiveDestinationBackend"
_FILESYSTEM = "core.sources.filesystem.FileSystemSourceBackend"
_RESANA = "core.sources.resana.backend.ResanaSourceBackend"
_KEYCLOAK = "https://kc.example.com/realms/ONHEXAGONE"


@pytest.fixture(autouse=True)
def filesystem_export(settings, tmp_path):
    """Run export against a local tree and the real archive destination."""
    settings.APP_WORK_DIR = str(tmp_path / "work")
    settings.FILESYSTEM_SOURCE_ROOT = str(tmp_path / "source")
    settings.SOURCE_BACKEND = _FILESYSTEM
    settings.DESTINATION_BACKENDS = [_ARCHIVE]
    settings.MIGRATION_FILE_LIMIT_PER_WORKSPACE = 0
    settings.LOGIN_REDIRECT_URL = "http://localhost:3010"
    settings.AWS_STORAGE_BUCKET_NAME = "bucket"
    settings.AWS_S3_DOWNLOAD_URL = ""
    DestinationRegistry.clear_cache()
    yield
    DestinationRegistry.clear_cache()


@pytest.fixture(autouse=True)
def s3_resource():
    """S3 is the one call archive export makes outside this codebase."""
    with patch("core.processing.folder_helper.boto3") as mock_boto3:
        resource = mock_boto3.resource.return_value
        resource.meta.client.generate_presigned_url.return_value = (
            "http://s3.example/ws.zip"
        )
        yield resource


@pytest.fixture()
def user():
    return factories.UserFactory()


@pytest.fixture()
def workspace(user):
    """A real workspace linked to the user, with integrity tracking off."""
    created = factories.WorkspaceFactory(
        title="My Workspace",
        is_file_integrity_tracked=False,
    )
    user.workspaces.add(created)
    return created


def _payload(workspace, user):
    return {
        "workspace": {"id": str(workspace.id)},
        "user": {"id": str(user.id)},
    }


def _workspace_dir(workspace):
    return f"{settings.APP_WORK_DIR}/workspace_{workspace.id}"


def _export(workspace, user):
    return export(_payload(workspace, user))  # pylint: disable=no-value-for-parameter


def _local_files(workspace):
    root = _workspace_dir(workspace)
    if not os.path.isdir(root):
        return []
    found = []
    for dirpath, _, filenames in os.walk(root):
        found.extend(os.path.join(dirpath, name) for name in filenames)
    return found


def _seed(user, workspace, files=()):
    """Lay a source tree under FILESYSTEM_SOURCE_ROOT and point the workspace at it."""
    root = Path(settings.FILESYSTEM_SOURCE_ROOT) / user.email / "espace"
    root.mkdir(parents=True)
    for name, content in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            os.symlink("missing-target", path)
        else:
            path.write_bytes(content)
    workspace.source_id = str(root)
    workspace.source_type = "filesystem"
    workspace.save()
    return root


def _set_statuses(workspace, **statuses):
    for name, status in statuses.items():
        workspace.set_destination_status(name, status)
    workspace.save()


def _run_task(task_id, workspace, user, **fields):
    task_result = TaskResult.objects.create(task_id=task_id)
    return ExtraTaskInfo.objects.create(
        task_result=task_result,
        workspace=workspace,
        user=user,
        **fields,
    )


def _sender(task_id):
    return SimpleNamespace(request=SimpleNamespace(id=task_id))


def _track(workspace):
    workspace.is_file_integrity_tracked = True
    workspace.save(update_fields=["is_file_integrity_tracked"])


def test_export_raises_when_the_workspace_does_not_exist(user):
    """export() loads the workspace from the database."""
    missing = {"workspace": {"id": str(uuid.uuid4())}, "user": {"id": str(user.id)}}
    with pytest.raises(Workspace.DoesNotExist):
        export(missing)  # pylint: disable=no-value-for-parameter


def test_export_raises_when_the_user_does_not_exist(workspace):
    """export() loads the user from the database."""
    missing = {
        "workspace": {"id": str(workspace.id)},
        "user": {"id": str(uuid.uuid4())},
    }
    with pytest.raises(User.DoesNotExist):
        export(missing)  # pylint: disable=no-value-for-parameter


def test_export_skips_file_truncation_when_limit_is_zero(workspace, user, settings):
    """export() keeps every source file when the limit is 0."""
    settings.MIGRATION_FILE_LIMIT_PER_WORKSPACE = 0
    _seed(user, workspace, [("kept.txt", b"a"), ("also.txt", b"b")])

    _export(workspace, user)

    assert len(_local_files(workspace)) == 2
    workspace.refresh_from_db()
    assert workspace.is_truncated is False


def test_export_truncates_files_when_limit_is_set_and_exceeded(
    workspace, user, settings
):
    """export() keeps only the limit and marks the workspace as truncated."""
    settings.MIGRATION_FILE_LIMIT_PER_WORKSPACE = 1
    _seed(user, workspace, [("kept.txt", b"a"), ("dropped.txt", b"b")])

    _export(workspace, user)

    assert len(_local_files(workspace)) == 1
    workspace.refresh_from_db()
    assert workspace.is_truncated is True


def test_export_does_not_flag_workspace_when_limit_is_set_but_not_exceeded(
    workspace, user, settings
):
    """export() does not mark the workspace as truncated when it's under the limit."""
    settings.MIGRATION_FILE_LIMIT_PER_WORKSPACE = 5
    _seed(user, workspace, [("only.txt", b"a")])

    _export(workspace, user)

    workspace.refresh_from_db()
    assert workspace.is_truncated is False
    assert len(_local_files(workspace)) == 1


def test_export_builds_local_folder(workspace, user):
    """export() copies the source tree into the workspace directory."""
    _seed(user, workspace, [("doc.txt", b"hello"), ("notes/page.txt", b"nested")])

    _export(workspace, user)

    assert Path(_workspace_dir(workspace), "doc.txt").read_bytes() == b"hello"
    assert (
        Path(_workspace_dir(workspace), "notes", "page.txt").read_bytes() == b"nested"
    )


def test_export_prepare_export_loads_members_from_the_source(workspace, user):
    """prepare_export() records the members listed in the source's _users.csv."""
    root = _seed(user, workspace, [("doc.txt", b"x")])
    (root / "_users.csv").write_text(
        "Dupont,Alice,alice@example.com\n", encoding="utf-8"
    )

    _export(workspace, user)

    workspace.refresh_from_db()
    assert workspace.members == [
        {"name": "Dupont", "firstName": "Alice", "email": "alice@example.com"}
    ]


def test_export_persists_download_errors_on_workspace(workspace, user):
    """export() stores the files it could not copy and drops the previous run's."""
    workspace.download_errors = [
        {"id": "old", "name": "old.txt", "path": "old.txt", "error": "previous"}
    ]
    workspace.save(update_fields=["download_errors"])
    _seed(
        user,
        workspace,
        [("broken.docx", None), ("ok.txt", b"fine")],
    )

    _export(workspace, user)

    workspace.refresh_from_db()
    assert [error["name"] for error in workspace.download_errors] == ["broken.docx"]
    assert Path(_workspace_dir(workspace), "ok.txt").read_bytes() == b"fine"


def test_export_stops_when_resana_refuses_the_offline_token(workspace, user, settings):
    """A 401 from Keycloak stops the run before any file is read, and the
    previous run's errors are already cleared."""
    settings.SOURCE_BACKEND = _RESANA
    settings.RESANA_KEYCLOAK_ENDPOINT = _KEYCLOAK
    settings.RESANA_MIGRATOR_CLIENT_ID = "resana-migrator"
    settings.RESANA_MIGRATOR_CLIENT_SECRET = "s3cr3t"
    settings.RESANA_MIGRATOR_REDIRECT_URI = "https://migrator.example.com/callback"
    settings.RESANA_RETRY_MAX_ATTEMPTS = 1
    settings.OIDC_TOKENS_ENCRYPTION_KEY = Fernet.generate_key().decode()
    workspace.source_type = "resana"
    workspace.source_id = "ged-uuid"
    workspace.migration_user = user
    workspace.download_errors = [
        {"id": "old", "name": "old.txt", "path": "old.txt", "error": "previous"}
    ]
    workspace.save()
    expired = jwt.encode(
        {"exp": int((timezone.now() - timedelta(hours=1)).timestamp())},
        "test-secret-long-enough-for-hs256",
        algorithm="HS256",
    )
    ResanaTokenManager(user).store_tokens(
        offline_token="offline-token",
        access_token=expired,
        session_id="sess",
        csrf_token="csrf",
    )
    _set_statuses(workspace, archive=Workspace.Status.PENDING)

    with responses.RequestsMock() as recorded:
        recorded.add(
            responses.POST,
            f"{_KEYCLOAK}/protocol/openid-connect/token",
            json={"error": "invalid_grant"},
            status=401,
        )
        with pytest.raises(ResanaTokenExpired, match="no longer valid"):
            _export(workspace, user)

        assert recorded.calls
        assert all(
            call.request.url.endswith("/protocol/openid-connect/token")
            for call in recorded.calls
        )

    workspace.refresh_from_db()
    assert workspace.download_errors == []
    assert workspace.get_destination_status("archive") == Workspace.Status.PENDING
    assert not mail.outbox


def test_export_resets_previous_run_errors_before_reading_the_source(workspace, user):
    """Errors and the previous rejected-files zip are cleared even when the
    source tree cannot be read."""
    workspace.download_errors = [
        {"id": "old", "name": "old.txt", "path": "old.txt", "error": "previous"}
    ]
    workspace.upload_errors = [{"path": "old.txt", "error": "refused"}]
    workspace.destination_metadata = {"archive": {"rejected_files_s3_key": "old.zip"}}
    workspace.source_id = "/tmp/drive-migrator-missing-source"
    workspace.save()

    with pytest.raises(FileNotFoundError):
        _export(workspace, user)

    workspace.refresh_from_db()
    assert workspace.download_errors == []
    assert workspace.upload_errors == []
    assert "rejected_files_s3_key" not in workspace.destination_metadata.get(
        "archive", {}
    )


def test_export_leaves_no_download_errors_when_all_files_succeed(workspace, user):
    """A fully successful download records no download error."""
    workspace.download_errors = [
        {"id": "old", "name": "old.txt", "path": "old.txt", "error": "previous"}
    ]
    workspace.save(update_fields=["download_errors"])
    _seed(user, workspace, [("ok.txt", b"fine")])

    _export(workspace, user)

    workspace.refresh_from_db()
    assert workspace.download_errors == []


def test_export_persists_partial_download_errors(workspace, user):
    """Some failed copies do not stop the run, and the archive is still built."""
    _set_statuses(workspace, archive=Workspace.Status.PENDING)
    _seed(user, workspace, [("broken.docx", None), ("ok.txt", b"fine")])

    assert _export(workspace, user) is True

    workspace.refresh_from_db()
    assert [error["name"] for error in workspace.download_errors] == ["broken.docx"]
    assert workspace.get_destination_status("archive") == Workspace.Status.SUCCESS
    assert len(mail.outbox) == 1


def test_export_fails_when_all_downloads_fail(workspace, user, s3_resource):
    """export() raises when every copy failed, and the archive is not uploaded."""
    _set_statuses(workspace, archive=Workspace.Status.PENDING)
    _seed(user, workspace, [("a.docx", None), ("b.docx", None)])

    with pytest.raises(RuntimeError, match="failed to download"):
        _export(workspace, user)

    s3_resource.Bucket.return_value.upload_file.assert_not_called()
    assert not mail.outbox
    workspace.refresh_from_db()
    assert workspace.get_destination_status("archive") == Workspace.Status.PENDING
    assert {error["name"] for error in workspace.download_errors} == {
        "a.docx",
        "b.docx",
    }


def test_export_uploads_the_archive_of_a_pending_destination(
    workspace, user, s3_resource
):
    """A pending archive destination is zipped, uploaded and mailed."""
    _set_statuses(workspace, archive=Workspace.Status.PENDING)
    _seed(user, workspace, [("doc.txt", b"hello")])

    assert _export(workspace, user) is True

    s3_resource.Bucket.return_value.upload_file.assert_called_once()
    workspace.refresh_from_db()
    assert workspace.get_destination_status("archive") == Workspace.Status.SUCCESS
    assert workspace.get_destination_metadata("archive")["s3_key"].endswith(".zip")
    assert len(mail.outbox) == 1
    assert (
        f"http://localhost:3010/download-archive/?workspaceId={workspace.id}"
        in mail.outbox[0].body
    )


def test_export_skips_an_archive_that_is_already_done(workspace, user, s3_resource):
    """export() does not upload again when the archive is already a success."""
    _set_statuses(workspace, archive=Workspace.Status.SUCCESS)
    _seed(user, workspace, [("doc.txt", b"hello")])

    _export(workspace, user)

    s3_resource.Bucket.return_value.upload_file.assert_not_called()
    assert not mail.outbox
    workspace.refresh_from_db()
    assert workspace.get_destination_status("archive") == Workspace.Status.SUCCESS


def test_export_saves_an_integrity_report_for_a_successful_run(workspace, user):
    """With tracking on, the run's report counts the file the archive received."""
    _track(workspace)
    _set_statuses(workspace, archive=Workspace.Status.PENDING)
    _seed(user, workspace, [("doc.txt", b"hello")])
    extra = _run_task("task-integrity-ok", workspace, user)

    result = export.apply(
        args=[_payload(workspace, user)],
        task_id=extra.task_result.task_id,
        throw=True,
    )

    assert result.result is True
    extra.refresh_from_db()
    summary = extra.integrity_report["summary"]
    assert summary["source_files_count"] == 1
    assert summary["migrated_files_count"] == 1
    assert summary["stages"] == {"ok": 1}
    assert extra.integrity_check_passed is True


def test_export_saves_the_integrity_report_when_the_archive_upload_fails(
    workspace, user, s3_resource
):
    """A destination that fails still leaves its report, and the error propagates."""
    _track(workspace)
    _set_statuses(workspace, archive=Workspace.Status.PENDING)
    _seed(user, workspace, [("doc.txt", b"hello")])
    extra = _run_task("task-integrity-s3", workspace, user)
    s3_resource.Bucket.return_value.upload_file.side_effect = RuntimeError("S3 down")

    with pytest.raises(RuntimeError, match="S3 down"):
        export.apply(
            args=[_payload(workspace, user)],
            task_id=extra.task_result.task_id,
            throw=True,
        )

    extra.refresh_from_db()
    assert extra.integrity_report["summary"]["local_files_count"] == 1
    assert extra.integrity_report["summary"]["stages"] == {"ok": 1}


def test_export_saves_the_integrity_report_when_all_downloads_fail(workspace, user):
    """The report is also saved when the run stops during retrieval."""
    _track(workspace)
    _seed(user, workspace, [("a.txt", None)])
    extra = _run_task("task-integrity-download", workspace, user)

    with pytest.raises(RuntimeError, match="failed to download"):
        export.apply(
            args=[_payload(workspace, user)],
            task_id=extra.task_result.task_id,
            throw=True,
        )

    extra.refresh_from_db()
    summary = extra.integrity_report["summary"]
    assert summary["stages"] == {"download_failed": 1}
    assert summary["migrated_files_count"] == 0


def test_export_does_not_save_an_integrity_report_when_tracking_is_off(workspace, user):
    """Tracking stays off when the workspace disables it: no report is stored."""
    _seed(user, workspace, [("doc.txt", b"hello")])
    extra = _run_task("task-integrity-off", workspace, user)

    export.apply(
        args=[_payload(workspace, user)],
        task_id=extra.task_result.task_id,
        throw=True,
    )

    extra.refresh_from_db()
    assert extra.integrity_report == {}


def test_export_raises_the_source_error_when_the_tree_cannot_be_read(workspace, user):
    """finalize_export() runs in a finally and must not replace the source error."""
    workspace.source_id = "/tmp/drive-migrator-missing-source"
    workspace.save(update_fields=["source_id"])

    with pytest.raises(FileNotFoundError):
        _export(workspace, user)


# ---------------------------------------------------------------------------
# list_work_dir
# ---------------------------------------------------------------------------


def test_list_work_dir_logs_files(tmp_path, settings):
    """list_work_dir() iterates over files and directories in APP_WORK_DIR."""
    settings.APP_WORK_DIR = str(tmp_path)
    (tmp_path / "file.txt").write_text("content")
    (tmp_path / "subdir").mkdir()

    list_work_dir()  # must not raise — logging is the side effect


# ---------------------------------------------------------------------------
# list_workspace_dir
# ---------------------------------------------------------------------------


def test_list_workspace_dir_logs_files(workspace):
    """list_workspace_dir() walks the workspace directory and logs each file."""
    workspace_dir = _workspace_dir(workspace)
    os.makedirs(workspace_dir)
    with open(os.path.join(workspace_dir, "doc.pdf"), "w", encoding="utf-8") as handle:
        handle.write("data")

    list_workspace_dir(workspace)  # must not raise


# ---------------------------------------------------------------------------
# cleanup_workspace_dir
# ---------------------------------------------------------------------------


def test_cleanup_workspace_dir_deletes_folder_and_archive(workspace):
    """cleanup_workspace_dir() deletes the local folder and the local zip."""
    workspace_dir = _workspace_dir(workspace)
    os.makedirs(workspace_dir)
    archive = f"{workspace_dir}.zip"
    with open(archive, "wb") as handle:
        handle.write(b"zip")

    cleanup_workspace_dir(workspace)

    assert not os.path.exists(workspace_dir)
    assert not os.path.exists(archive)


# ---------------------------------------------------------------------------
# debug_folder
# ---------------------------------------------------------------------------


def test_debug_folder_recurses_into_children():
    """debug_folder() recurses into nested SourceFolders without error."""
    folder = SourceFolder(
        name="root",
        children=[
            SourceFolder(name="child", children=[SourceFolder(name="grandchild")])
        ],
    )

    debug_folder(folder)  # must not raise


# ---------------------------------------------------------------------------
# create_task_result_on_publish
# ---------------------------------------------------------------------------


def test_create_task_result_on_publish_stores_result():
    """create_task_result_on_publish() stores a PENDING TaskResult when 'task' is set."""
    headers = {
        "task": "core.processing.tasks.export",
        "id": "task-uuid-123",
        "argsrepr": "[]",
        "kwargsrepr": "{}",
    }

    create_task_result_on_publish(headers=headers)

    stored = TaskResult.objects.get(task_id="task-uuid-123")
    assert stored.task_name == "core.processing.tasks.export"
    assert stored.status == "PENDING"


def test_create_task_result_on_publish_skips_when_no_task_key():
    """create_task_result_on_publish() returns early when 'task' is not in headers."""
    create_task_result_on_publish(headers={"id": "some-id"})

    assert not TaskResult.objects.filter(task_id="some-id").exists()


# ---------------------------------------------------------------------------
# task_success signal handler
# ---------------------------------------------------------------------------


def test_task_success_handler_saves_workspace_and_cleans_up(workspace, user):
    """task_success() saves the workspace and deletes its local folder and archive."""
    extra = _run_task("task-id-1", workspace, user)
    os.makedirs(_workspace_dir(workspace))

    archive = f"{_workspace_dir(workspace)}.zip"
    with open(archive, "wb") as handle:
        handle.write(b"zip")

    task_success_handler(sender=_sender(extra.task_result.task_id))

    assert not os.path.exists(_workspace_dir(workspace))
    assert not os.path.exists(archive)


# ---------------------------------------------------------------------------
# task_failure signal handler
# ---------------------------------------------------------------------------


def test_task_failure_handler_sets_pending_statuses_to_failure(workspace, user):
    """task_failure() sets PENDING destination statuses to FAILURE."""
    _set_statuses(
        workspace,
        archive=Workspace.Status.PENDING,
        resana=Workspace.Status.SUCCESS,
    )
    _run_task("task-id-2", workspace, user)

    task_failure_handler(sender=_sender("task-id-2"))

    workspace.refresh_from_db()
    assert workspace.get_destination_status("archive") == Workspace.Status.FAILURE
    assert workspace.get_destination_status("resana") == Workspace.Status.SUCCESS


def test_task_failure_handler_sends_fail_mail(workspace, user):
    """task_failure() sends a failure notification email."""
    _run_task("task-id-3", workspace, user)

    task_failure_handler(sender=_sender("task-id-3"))

    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == [user.email]
    assert workspace.title in sent.body


def test_task_failure_handler_asks_to_log_in_again_on_expired_source_token(
    workspace, user
):
    """A run that failed on an expired source token tells the user to log in
    to the source again before relaunching."""
    _run_task("task-id-4", workspace, user)

    task_failure_handler(
        sender=_sender("task-id-4"),
        exception=ResanaTokenExpired("Resana offline token is no longer valid."),
    )

    assert len(mail.outbox) == 1
    assert "Reconnectez-vous" in mail.outbox[0].body


# ---------------------------------------------------------------------------
# migration_finished analytics
# ---------------------------------------------------------------------------


def _assert_captured(capture, user, workspace, status):
    assert capture.call_count == 1
    event = capture.call_args.args[0]
    properties = capture.call_args.kwargs["properties"]
    assert event == "migration_finished"
    assert capture.call_args.kwargs["distinct_id"] == user.email
    assert properties["workspace_id"] == str(workspace.id)
    assert properties["status"] == status
    return properties


@pytest.mark.parametrize(
    "handler,status",
    [(task_success_handler, "success"), (task_failure_handler, "failure")],
)
def test_task_handlers_capture_migration_finished(
    handler, status, workspace, user, settings
):
    """Both end-of-task handlers emit migration_finished with the final status."""
    settings.POSTHOG_KEY = "phc_test"
    task_result = TaskResult.objects.create(task_id=f"task-finished-{status}")
    task_result.date_created = timezone.now() - timedelta(seconds=42)
    task_result.save(update_fields=["date_created"])
    ExtraTaskInfo.objects.create(
        task_result=task_result,
        workspace=workspace,
        user=user,
        integrity_check_passed=False,
        integrity_report={
            "summary": {
                "source_files_count": 7,
                "migrated_files_count": 5,
                "stages": {"ok": 5, "analysis_unfinished": 2},
            }
        },
    )
    workspace.is_truncated = True
    workspace.download_errors = [
        {"id": "a", "name": "a.txt", "path": "a.txt", "error": "x"},
        {"id": "b", "name": "b.txt", "path": "b.txt", "error": "y"},
    ]
    workspace.upload_errors = [
        {"path": "c.txt", "error": "refused", "destination": "drive"}
    ]
    workspace.save()
    folder = _workspace_dir(workspace)
    os.makedirs(folder)
    with open(os.path.join(folder, "note.txt"), "wb") as handle:
        handle.write(b"abc")

    with patch("posthog.capture") as capture:
        handler(sender=_sender(task_result.task_id))

    properties = _assert_captured(capture, user, workspace, status)
    assert properties["is_truncated"] is True
    assert properties["download_errors_count"] == 2
    assert properties["upload_errors_count"] == 1
    assert properties["integrity_check_passed"] is False
    assert properties["migrated_files_count"] == 5
    assert properties["source_files_count"] == 7
    assert properties["analysis_unfinished_count"] == 2
    assert 42 <= properties["duration_seconds"] < 60
    assert properties["$set"] == {
        "workspaces_found": 1,
        "workspaces_not_migrated": 1,
    }
    assert properties["workspace_size_bytes"] == 3


def test_migration_finished_file_counts_are_none_without_integrity_report(
    workspace, user, settings
):
    """Without integrity check, the file counts are sent as None."""
    settings.POSTHOG_KEY = "phc_test"
    extra = _run_task("task-id-5", workspace, user)

    with patch("posthog.capture") as capture:
        task_success_handler(sender=_sender(extra.task_result.task_id))

    properties = capture.call_args.kwargs["properties"]
    assert properties["migrated_files_count"] is None
    assert properties["source_files_count"] is None
    assert properties["analysis_unfinished_count"] is None


def test_migration_finished_counts_zero_unfinished_analyses_in_a_report(
    workspace, user, settings
):
    """A report without file under analysis sends 0, not None."""
    settings.POSTHOG_KEY = "phc_test"
    extra = _run_task(
        "task-id-6",
        workspace,
        user,
        integrity_report={
            "summary": {
                "source_files_count": 1,
                "migrated_files_count": 1,
                "stages": {"ok": 1},
            }
        },
    )

    with patch("posthog.capture") as capture:
        task_success_handler(sender=_sender(extra.task_result.task_id))

    assert capture.call_args.kwargs["properties"]["analysis_unfinished_count"] == 0


def test_export_logs_with_the_workspace(workspace, user, console_logs):
    """The logs of a migration should tell its workspace."""
    _seed(user, workspace)

    _export(workspace, user)

    task_logs = [
        log for log in console_logs if log["logger"] == "core.processing.tasks"
    ]
    workspace_id = str(workspace.id)
    assert task_logs[0] == {
        "event": f"Starting workspace {workspace_id} ...",
        "level": "info",
        "logger": "core.processing.tasks",
        "workspace_id": workspace_id,
    }
    assert task_logs[-1] == {
        "event": "Task done",
        "level": "info",
        "logger": "core.processing.tasks",
        "workspace_id": workspace_id,
    }


def test_export_logs_token_status_before_migrating(workspace, user, settings, caplog):
    """The tokens of the services the workspace relies on are logged at task start."""
    settings.DRIVE_AUTH_MODE = "user_token"
    _seed(user, workspace)
    workspace.source_type = "resana"
    _set_statuses(workspace, drive=Workspace.Status.PENDING)

    with caplog.at_level(logging.INFO):
        _export(workspace, user)

    text = caplog.text
    assert f"Before migrating workspace {workspace.id}" in text
    assert f"Resana tokens for user {user.id}" in text
    assert f"Drive (ProConnect) tokens for user {user.id}" in text


def test_export_logs_token_status_only_for_used_services(workspace, user, caplog):
    """No token is described for a source or destination the workspace doesn't use."""
    _seed(user, workspace)
    workspace.source_type = "osmose"
    workspace.save(update_fields=["source_type"])

    with caplog.at_level(logging.INFO):
        _export(workspace, user)

    assert f"Before migrating workspace {workspace.id}" not in caplog.text
    assert "Resana tokens" not in caplog.text
    assert "Drive (ProConnect) tokens" not in caplog.text
