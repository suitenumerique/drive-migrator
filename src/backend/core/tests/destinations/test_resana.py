"""Tests for ResanaDestinationBackend."""

from unittest.mock import MagicMock, patch

import pytest
from boto3.exceptions import S3UploadFailedError

from core.backends.destination import (
    AbstractDestinationBackend,
    FileExportStatus,
    RejectedFile,
)
from core.destinations.resana.backend import ResanaDestinationBackend
from core.destinations.resana.resana_backend import ResanaBackend
from core.factories import UserFactory, WorkspaceFactory
from core.models import ResanaEmailMapping, Workspace
from core.processing.file_exports import FileExports

LONG = "x" * 60
CUT = "x" * 57


@pytest.fixture(name="resana")
def fixture_resana():
    """The Resana API behind the destination."""
    with patch("core.destinations.resana.backend.ResanaBackend") as mock_cls:
        mock_cls.return_value.prepare_import.return_value = {
            "user_uuid": "user-uuid",
            "organization_uuid": "org-uuid",
        }
        yield mock_cls.return_value


@pytest.fixture(name="bucket")
def fixture_bucket():
    """In-memory bucket Resana imports from, as {key: bytes}."""
    objects = {}
    with patch("core.destinations.resana.backend.S3ResanaManager") as mock_cls:
        manager = mock_cls.return_value

        def upload_file(path, key):
            with open(path, "rb") as f:
                objects[key] = f.read()

        def put_object(key, body):
            objects[key] = body

        def delete_prefix(prefix):
            for key in [k for k in objects if k.startswith(prefix + "/")]:
                del objects[key]

        manager.get_destination_path.side_effect = lambda ws: f"workspace_{ws.id}"
        manager.upload_file.side_effect = upload_file
        manager.put_object.side_effect = put_object
        manager.delete_prefix.side_effect = delete_prefix
        manager.objects = objects
        yield manager


@pytest.fixture(name="rejected_files_archive", autouse=True)
def fixture_rejected_files_archive():
    """Capture what the export hands to the rejected files archive, which would
    otherwise reach S3."""
    with patch("core.destinations.resana.backend.RejectedFilesArchive") as mock_cls:
        yield mock_cls


@pytest.fixture(name="export_run")
def fixture_export_run(tmp_path):
    """Export a run to Resana file by file, as the export task does; return the
    failed destinations by name."""

    def export(run, user=None):
        exports = FileExports(run, user or UserFactory(), [ResanaDestinationBackend()])
        exports.open()
        for migration_file in run.files.all():
            local_path = tmp_path / migration_file.path
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(f"content of {migration_file.path}".encode())
            exports.put(migration_file, str(local_path))
        return exports.close()

    return export


def test_implements_abstract_destination():
    """ResanaDestinationBackend must be a concrete AbstractDestinationBackend."""
    assert issubclass(ResanaDestinationBackend, AbstractDestinationBackend)


def test_name_is_resana():
    """name must be 'resana'."""
    assert ResanaDestinationBackend.name == "resana"


def test_label_is_set():
    """label must be a non-empty string."""
    assert isinstance(ResanaDestinationBackend.label, str)
    assert ResanaDestinationBackend.label != ""


def test_exports_file_by_file():
    """Resana receives the run file by file, nothing waits on disk for it."""
    assert ResanaDestinationBackend.exports_file_by_file is True


@pytest.mark.django_db
@pytest.mark.usefixtures("bucket")
def test_open_checks_resana_before_any_file(resana, make_run):
    """The Resana account and organization are checked before the downloads,
    instead of once everything is downloaded."""
    run = make_run()
    user = UserFactory()

    ResanaDestinationBackend().open_export(user, run)

    resana.prepare_import.assert_called_once_with(run.workspace, user)


@pytest.mark.django_db
def test_open_fails_the_export_when_resana_refuses_it(
    resana, bucket, make_run, export_run
):
    """A user unknown to Resana gets no file sent, and the export fails."""
    resana.prepare_import.side_effect = ValueError("User not found in Resana")
    run = make_run(paths=("a.txt",))

    failures = export_run(run)

    assert "resana" in failures
    bucket.upload_file.assert_not_called()
    resana.create_import.assert_not_called()


@pytest.mark.django_db
@pytest.mark.usefixtures("resana")
def test_open_clears_what_a_previous_run_left(bucket, make_run):
    """Files a former run left under the workspace's prefix are not imported."""
    run = make_run()
    bucket.objects[f"workspace_{run.workspace.id}/old.txt"] = b"old"

    ResanaDestinationBackend().open_export(UserFactory(), run)

    assert not bucket.objects


@pytest.mark.django_db
@pytest.mark.usefixtures("resana")
def test_put_stores_each_file_at_its_path(bucket, make_run, export_run):
    """Each file lands under the workspace's prefix, at its path in the plan."""
    run = make_run(paths=("docs/report.pdf",))

    assert export_run(run) == {}

    key = f"workspace_{run.workspace.id}/docs/report.pdf"
    assert bucket.objects[key] == b"content of docs/report.pdf"
    assert run.files.get().results["resana"] == {"status": FileExportStatus.SENT}


@pytest.mark.django_db
@pytest.mark.usefixtures("resana")
def test_put_truncates_folder_names_too_long_for_resana(bucket, make_run, export_run):
    """Folder names over 57 characters are truncated, as Resana requires."""
    run = make_run(paths=(f"{LONG}/a.txt",))

    export_run(run)

    assert f"workspace_{run.workspace.id}/{CUT}/a.txt" in bucket.objects


@pytest.mark.django_db
@pytest.mark.usefixtures("resana")
def test_put_marks_the_file_failed_when_s3_refuses_it(bucket, make_run):
    """An S3 error loses this file only: the export goes on with the next ones."""
    run = make_run(paths=("a.txt",))
    backend = ResanaDestinationBackend()
    backend.open_export(UserFactory(), run)
    bucket.upload_file.side_effect = S3UploadFailedError("S3 down")
    migration_file = run.files.get()

    backend.put_file(UserFactory(), run, migration_file, "/nowhere/a.txt")

    assert migration_file.results["resana"] == {
        "status": FileExportStatus.FAILED,
        "error": "S3 down",
    }


@pytest.mark.django_db
@pytest.mark.usefixtures("resana")
def test_close_adds_the_members_listing(bucket, make_run, export_run):
    """The members go to osmose_users.csv at the root, which Resana imports."""
    run = make_run(
        members=[
            {"name": "Dupont", "firstName": "Jean", "email": "jean@example.com"},
            {"name": "Martin", "firstName": "Alice", "email": "alice@example.com"},
        ]
    )

    export_run(run)

    assert bucket.objects[f"workspace_{run.workspace.id}/osmose_users.csv"] == (
        b"Dupont,Jean,jean@example.com\r\nMartin,Alice,alice@example.com\r\n"
    )


@pytest.mark.django_db
@pytest.mark.usefixtures("resana")
def test_close_skips_the_members_listing_without_members(bucket, make_run, export_run):
    """No osmose_users.csv without members."""
    run = make_run(members=[])

    export_run(run)

    assert f"workspace_{run.workspace.id}/osmose_users.csv" not in bucket.objects


@pytest.mark.django_db
@pytest.mark.usefixtures("bucket")
def test_close_creates_the_import_job(resana, make_run, export_run):
    """Once every file is stored, the Resana workspace and its import job are
    created, and the destination waits for the job: it stays pending."""
    run = make_run(destination_statuses={"resana": Workspace.Status.PENDING})

    assert export_run(run) == {}

    resana.create_import.assert_called_once_with(
        run.workspace, "user-uuid", "org-uuid", f"workspace_{run.workspace.id}"
    )
    run.workspace.refresh_from_db()
    assert run.workspace.get_destination_status("resana") == Workspace.Status.PENDING


@pytest.mark.django_db
@pytest.mark.usefixtures("resana")
def test_close_hands_the_failed_files_back_to_the_user(
    bucket, make_run, export_run, rejected_files_archive
):
    """A file S3 did not store goes to the rejected files archive of the run."""
    run = make_run(paths=("a.txt", "b.txt"))
    upload_file = bucket.upload_file.side_effect

    def fail_on_b(path, key):
        if path.endswith("b.txt"):
            raise S3UploadFailedError("S3 down")
        upload_file(path, key)

    bucket.upload_file.side_effect = fail_on_b

    assert export_run(run) == {}

    handed_run, lost = rejected_files_archive.return_value.build.call_args.args
    assert handed_run.pk == run.pk
    assert lost == [RejectedFile("b.txt", "upload_failed")]


@pytest.mark.django_db
def test_close_fails_when_no_file_was_stored(
    resana, bucket, make_run, export_run, rejected_files_archive
):
    """No import without any of the files, but the files go back to the user."""
    run = make_run(paths=("a.txt",))
    bucket.upload_file.side_effect = S3UploadFailedError("S3 down")

    failures = export_run(run)

    assert "resana" in failures
    resana.create_import.assert_not_called()
    rejected_files_archive.return_value.build.assert_called_once()


@pytest.mark.django_db
@pytest.mark.usefixtures("resana")
def test_abort_deletes_what_the_run_stored(bucket, make_run, tmp_path):
    """A failed export leaves nothing to import."""
    run = make_run(paths=("a.txt",))
    backend = ResanaDestinationBackend()
    backend.open_export(UserFactory(), run)
    local_path = tmp_path / "a.txt"
    local_path.write_bytes(b"a")
    backend.put_file(UserFactory(), run, run.files.get(), str(local_path))

    backend.abort_export(run)

    assert not bucket.objects


@pytest.mark.django_db
@pytest.mark.usefixtures("resana")
def test_abort_keeps_the_files_of_a_created_job(bucket, make_run, tmp_path):
    """Once its import job exists, Resana reads the files: they stay."""
    run = make_run(paths=("a.txt",))
    backend = ResanaDestinationBackend()
    backend.open_export(UserFactory(), run)
    local_path = tmp_path / "a.txt"
    local_path.write_bytes(b"a")
    backend.put_file(UserFactory(), run, run.files.get(), str(local_path))
    run.workspace.set_destination_metadata("resana", {"id": "r", "job_id": "j"})

    backend.abort_export(run)

    assert bucket.objects


@pytest.mark.django_db
def test_abort_before_open_does_nothing(bucket, make_run):
    """Nothing was stored when the export failed before opening."""
    ResanaDestinationBackend().abort_export(make_run())

    bucket.delete_prefix.assert_not_called()


def test_describe_rejection_explains_a_failed_upload():
    """The CSV of the rejected files tells why a file is not in Resana."""
    reason = ResanaDestinationBackend().describe_rejection("upload_failed")

    assert reason == "Fichier non envoyé vers Resana (erreur d'envoi)"


# --- ResanaBackend ---


@pytest.fixture(name="api")
def fixture_api():
    """ResanaBackend's calls to the Resana API, answered with a uuid."""
    with patch.object(ResanaBackend, "request") as mock_request:
        mock_request.return_value.json.return_value = {"uuid": "created-uuid"}
        yield mock_request


@pytest.mark.django_db
def test_prepare_import_returns_the_resana_user_and_organization(api, settings):
    """prepare_import() checks the user and the organization exist in Resana."""
    settings.RESANA_DEFAULT_ORGANIZATION = "org-uuid"
    workspace = WorkspaceFactory()
    backend = ResanaBackend()

    with patch.object(backend, "fetch_user", return_value={"uuid": "user-uuid"}):
        state = backend.prepare_import(workspace, UserFactory())

    assert state == {"user_uuid": "user-uuid", "organization_uuid": "org-uuid"}
    api.assert_called_once_with("get", "/organizations/org-uuid")


@pytest.mark.django_db
@pytest.mark.usefixtures("api")
def test_prepare_import_refuses_a_user_unknown_to_resana():
    """The migration needs the user's Resana account."""
    backend = ResanaBackend()

    with (
        patch.object(backend, "fetch_user", return_value=None),
        pytest.raises(ValueError, match="not found in Resana"),
    ):
        backend.prepare_import(WorkspaceFactory(), UserFactory())


@pytest.mark.django_db
@pytest.mark.usefixtures("api")
def test_prepare_import_refuses_a_workspace_already_in_resana():
    """A workspace is imported to Resana once."""
    workspace = WorkspaceFactory()
    workspace.set_destination_metadata("resana", {"id": "existing"})

    with pytest.raises(ValueError, match="already created"):
        ResanaBackend().prepare_import(workspace, UserFactory())


@pytest.mark.django_db
def test_create_import_creates_the_workspace_its_admin_and_the_job(api):
    """create_import() records the Resana workspace and job, keeping what the
    export already linked (the zip of the rejected files)."""
    workspace = WorkspaceFactory(title="My/Workspace")
    workspace.set_destination_metadata("resana", {"rejected_files_s3_key": "r.zip"})

    ResanaBackend().create_import(workspace, "user-uuid", "org-uuid", "workspace_1")

    calls = [(c.args, c.kwargs.get("json")) for c in api.call_args_list]
    assert calls == [
        (
            ("post", "/workspaces"),
            {"name": "MyWorkspace", "organizationUuid": "org-uuid", "color": "#ffffff"},
        ),
        (
            ("post", "/api-workspaces/members"),
            {
                "userUuid": "user-uuid",
                "workspaceUuid": "created-uuid",
                "profileCode": "GESTIONNAIRE",
            },
        ),
        (
            ("post", "/jobs"),
            {
                "type": "import",
                "destinationWorkspaceUuid": "created-uuid",
                "importPath": "workspace_1",
            },
        ),
    ]
    workspace.refresh_from_db()
    assert workspace.get_destination_metadata("resana") == {
        "rejected_files_s3_key": "r.zip",
        "id": "created-uuid",
        "job_id": "created-uuid",
    }


def test_get_error_details():
    """get_error_details() delegates to ResanaBackend.get_error_details()."""
    workspace = MagicMock(spec=Workspace)
    expected = [{"task": "failed"}]

    with patch("core.destinations.resana.backend.ResanaBackend") as mock_backend_cls:
        mock_backend = mock_backend_cls.return_value
        mock_backend.get_error_details.return_value = expected

        backend = ResanaDestinationBackend()
        result = backend.get_error_details(workspace)

    mock_backend.get_error_details.assert_called_once_with(workspace)
    assert result == expected


def test_retry_job():
    """retry() delegates to ResanaBackend.retry_job()."""
    workspace = MagicMock(spec=Workspace)

    with patch("core.destinations.resana.backend.ResanaBackend") as mock_backend_cls:
        mock_backend = mock_backend_cls.return_value

        backend = ResanaDestinationBackend()
        backend.retry(workspace)

    mock_backend.retry_job.assert_called_once_with(workspace)


def test_poll_completion():
    """poll_completion() delegates to ResanaBackend.refresh_job()."""
    workspace = MagicMock(spec=Workspace)

    with patch("core.destinations.resana.backend.ResanaBackend") as mock_backend_cls:
        mock_backend = mock_backend_cls.return_value

        backend = ResanaDestinationBackend()
        backend.poll_completion(workspace)

    mock_backend.refresh_job.assert_called_once_with(workspace)


@pytest.mark.django_db
def test_get_mapping_from_email_domain_match():
    """get_mapping_from_email() returns the mapping for an exact domain match."""
    mapping = ResanaEmailMapping.objects.create(
        domain="example.com", resana_organization_uuid="uuid-org-1"
    )

    rb = ResanaBackend()
    result = rb.get_mapping_from_email("user@example.com")

    assert result == mapping


@pytest.mark.django_db
def test_get_mapping_from_email_wildcard_fallback():
    """get_mapping_from_email() falls back to the wildcard '*' mapping."""
    wildcard = ResanaEmailMapping.objects.create(
        domain="*", resana_organization_uuid="uuid-default"
    )

    rb = ResanaBackend()
    result = rb.get_mapping_from_email("user@unknown.com")

    assert result == wildcard


@pytest.mark.django_db
def test_get_mapping_from_email_no_mapping_raises():
    """get_mapping_from_email() raises when no domain and no wildcard match."""
    rb = ResanaBackend()
    with pytest.raises(Exception, match="No default mapping found"):
        rb.get_mapping_from_email("user@unknown.com")
