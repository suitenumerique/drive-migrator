"""Tests for WorkspacesViewset: auth, download_archive and integrity."""

import io
import json
import zipfile
from unittest.mock import patch

from django.core.cache import cache
from django.core.signals import request_finished
from django.db import close_old_connections

import pytest
from django_celery_results.models import TaskResult
from rest_framework.test import APIClient

from core import factories
from core.models import ExtraTaskInfo

pytestmark = pytest.mark.django_db


def test_list_workspaces_anonymous():
    """Anonymous users must not access the workspaces list."""
    client = APIClient()
    response = client.get("/api/v1.0/workspaces/")
    assert response.status_code == 401


def test_download_archive_anonymous():
    """Anonymous users must not be able to request a download URL."""
    workspace = factories.WorkspaceFactory()
    client = APIClient()
    response = client.get(f"/api/v1.0/workspaces/{workspace.id}/download_archive/")
    assert response.status_code == 401


def test_download_archive_other_user_workspace_not_found():
    """A workspace not owned by the requesting user must not be accessible."""
    user = factories.UserFactory()
    other_workspace = factories.WorkspaceFactory()
    client = APIClient()
    client.force_login(user)

    response = client.get(
        f"/api/v1.0/workspaces/{other_workspace.id}/download_archive/"
    )
    assert response.status_code == 404


def test_download_archive_authenticated_owner():
    """The owning authenticated user gets a presigned download URL."""
    user = factories.UserFactory()
    workspace = factories.WorkspaceFactory()
    user.workspaces.add(workspace)
    client = APIClient()
    client.force_login(user)

    with patch(
        "core.api.views.workspaces.ArchiveManager.get_download_url"
    ) as mock_get_url:
        mock_get_url.return_value = "http://s3.example.com/ws.zip?token=abc"
        response = client.get(f"/api/v1.0/workspaces/{workspace.id}/download_archive/")

    assert response.status_code == 200
    assert response.json() == {"url": "http://s3.example.com/ws.zip?token=abc"}


# ---------------------------------------------------------------------------
# archive_zip
# ---------------------------------------------------------------------------

_MANIFEST_KEY = "archives/ws/run/manifest.json"


@pytest.fixture(name="archive_bucket")
def fixture_archive_bucket(settings):
    """An archive of two files on S3, behind the download module, with the
    download slots counted in a cache of this test only."""
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    cache.clear()
    objects = {
        _MANIFEST_KEY: json.dumps(
            {
                "dirs": ["docs"],
                "files": [
                    {"path": "a.txt", "key": "k1", "size": 5},
                    {"path": "docs/b.txt", "key": "k2", "size": 5},
                ],
            }
        ).encode(),
        "k1": b"hello",
        "k2": b"world",
    }
    with patch("core.destinations.archive.download.ArchiveManager") as mock_cls:
        manager = mock_cls.return_value
        manager.read_object.side_effect = lambda key: objects[key]
        manager.iter_object.side_effect = lambda key: iter([objects[key]])
        yield manager


def _archived_workspace(**fields):
    return factories.WorkspaceFactory(
        destination_metadata={"archive": {"manifest_key": _MANIFEST_KEY}}, **fields
    )


def _archive_zip_url(workspace):
    return f"/api/v1.0/workspaces/{workspace.id}/archive_zip/"


def test_download_archive_links_the_streamed_zip_of_a_manifest():
    """An archive made of a manifest is streamed by the backend, so the URL
    handed to the frontend is the backend's own, behind the user's session."""
    workspace = _archived_workspace()

    response = _owner_client(workspace).get(
        f"/api/v1.0/workspaces/{workspace.id}/download_archive/"
    )

    assert response.status_code == 200
    assert response.json() == {
        "url": f"http://testserver/api/v1.0/workspaces/{workspace.id}/archive_zip/"
    }


def test_archive_zip_anonymous():
    """Anonymous users must not download an archive."""
    workspace = _archived_workspace()

    response = APIClient().get(_archive_zip_url(workspace))

    assert response.status_code == 401


def test_archive_zip_other_user_workspace_not_found():
    """A workspace not owned by the requesting user must not be accessible."""
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)

    response = client.get(_archive_zip_url(_archived_workspace()))

    assert response.status_code == 404


def test_archive_zip_not_found_without_manifest():
    """A workspace archived by the former export has no zip to stream."""
    workspace = factories.WorkspaceFactory(
        destination_metadata={"archive": {"s3_key": "ws.zip"}}
    )

    response = _owner_client(workspace).get(_archive_zip_url(workspace))

    assert response.status_code == 404


@pytest.mark.usefixtures("archive_bucket")
def test_archive_zip_streams_the_archive():
    """The zip is streamed with its exact size, named after the workspace,
    and without proxy buffering, which would hold it on disk."""
    workspace = _archived_workspace(title="Équipe/projets")

    response = _owner_client(workspace).get(_archive_zip_url(workspace))

    assert response.status_code == 200
    content = b"".join(response.streaming_content)
    assert response["Content-Type"] == "application/zip"
    assert response["Content-Length"] == str(len(content))
    assert response["X-Accel-Buffering"] == "no"
    assert response["Content-Disposition"] == (
        "attachment; filename*=utf-8''%C3%89quipe_projets.zip"
    )
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert archive.read("a.txt") == b"hello"
        assert archive.read("docs/b.txt") == b"world"


@pytest.mark.usefixtures("archive_bucket")
def test_archive_zip_refuses_downloads_beyond_the_limit(settings):
    """Downloads beyond the limit are told to retry later, so the API keeps
    threads to answer."""
    settings.ARCHIVE_MAX_CONCURRENT_DOWNLOADS = 1
    workspace = _archived_workspace()
    client = _owner_client(workspace)

    first = client.get(_archive_zip_url(workspace))
    second = client.get(_archive_zip_url(workspace))

    assert first.status_code == 200
    assert second.status_code == 429


@pytest.mark.usefixtures("archive_bucket")
def test_archive_zip_releases_its_slot_once_the_response_is_closed(settings):
    """A finished or interrupted download frees its slot."""
    settings.ARCHIVE_MAX_CONCURRENT_DOWNLOADS = 1
    workspace = _archived_workspace()
    client = _owner_client(workspace)

    first = client.get(_archive_zip_url(workspace))
    # As the test client does, so that closing keeps the test's connection.
    request_finished.disconnect(close_old_connections)
    try:
        first.close()
    finally:
        request_finished.connect(close_old_connections)

    assert client.get(_archive_zip_url(workspace)).status_code == 200


def test_archive_zip_releases_its_slot_when_the_manifest_cannot_be_read(
    archive_bucket, settings
):
    """A download failing before streaming does not keep its slot."""
    settings.ARCHIVE_MAX_CONCURRENT_DOWNLOADS = 1
    workspace = _archived_workspace()
    client = _owner_client(workspace)
    client.raise_request_exception = False
    read_object = archive_bucket.read_object.side_effect
    archive_bucket.read_object.side_effect = RuntimeError("S3 down")

    assert client.get(_archive_zip_url(workspace)).status_code == 500

    archive_bucket.read_object.side_effect = read_object
    assert client.get(_archive_zip_url(workspace)).status_code == 200


# ---------------------------------------------------------------------------
# download_rejected_files
# ---------------------------------------------------------------------------


def _rejected_files_url(workspace, destination="drive"):
    return (
        f"/api/v1.0/workspaces/{workspace.id}/download_rejected_files/"
        f"?destination={destination}"
    )


def _owner_client(workspace):
    user = factories.UserFactory()
    user.workspaces.add(workspace)
    client = APIClient()
    client.force_login(user)
    return client


def test_download_rejected_files_anonymous():
    """Anonymous users must not be able to request a download URL."""
    workspace = factories.WorkspaceFactory()

    response = APIClient().get(_rejected_files_url(workspace))

    assert response.status_code == 401


def test_download_rejected_files_other_user_workspace_not_found():
    """A workspace not owned by the requesting user must not be accessible."""
    user = factories.UserFactory()
    other_workspace = factories.WorkspaceFactory(
        destination_metadata={"drive": {"rejected_files_s3_key": "ws.zip"}}
    )
    client = APIClient()
    client.force_login(user)

    response = client.get(_rejected_files_url(other_workspace))

    assert response.status_code == 404


@pytest.mark.parametrize("query", ["", "?destination=", "?destination=unknown"])
def test_download_rejected_files_requires_a_known_destination(query):
    """The destination whose refused files are wanted must be registered."""
    workspace = factories.WorkspaceFactory()

    response = _owner_client(workspace).get(
        f"/api/v1.0/workspaces/{workspace.id}/download_rejected_files/{query}"
    )

    assert response.status_code == 400


def test_download_rejected_files_not_found_without_zip():
    """No URL when the destination refused nothing in the last run."""
    workspace = factories.WorkspaceFactory(destination_metadata={"drive": {}})

    response = _owner_client(workspace).get(_rejected_files_url(workspace))

    assert response.status_code == 404


def test_download_rejected_files_authenticated_owner():
    """The owner gets a presigned URL of the destination's zip."""
    workspace = factories.WorkspaceFactory(
        destination_metadata={"drive": {"rejected_files_s3_key": "ws.zip"}}
    )

    with patch(
        "core.processing.rejected_files.ArchiveManager.get_presigned_url",
        return_value="http://s3.example.com/ws.zip?token=abc",
    ) as get_presigned_url:
        response = _owner_client(workspace).get(_rejected_files_url(workspace))

    assert response.status_code == 200
    assert response.json() == {"url": "http://s3.example.com/ws.zip?token=abc"}
    get_presigned_url.assert_called_once_with("ws.zip")


_REJECTED_MANIFEST_KEY = "rejected/ws/run/drive/manifest.json"


def _rejected_workspace(**fields):
    return factories.WorkspaceFactory(
        destination_metadata={
            "drive": {"rejected_files_manifest_key": _REJECTED_MANIFEST_KEY}
        },
        **fields,
    )


def _rejected_files_zip_url(workspace, destination="drive"):
    return (
        f"/api/v1.0/workspaces/{workspace.id}/rejected_files_zip/"
        f"?destination={destination}"
    )


@pytest.fixture(name="rejected_bucket")
def fixture_rejected_bucket(archive_bucket):
    """The refused files of a run on S3, next to the archive of two files."""
    objects = {
        _REJECTED_MANIFEST_KEY: json.dumps(
            {
                "dirs": [],
                "files": [
                    {"path": "docs/big.iso", "key": "r1", "size": 3},
                    {"path": "fichiers-non-migres.csv", "key": "r2", "size": 4},
                ],
            }
        ).encode(),
        "r1": b"iso",
        "r2": b"csv;",
    }
    archive_bucket.read_object.side_effect = lambda key: objects[key]
    archive_bucket.iter_object.side_effect = lambda key: iter([objects[key]])
    return archive_bucket


def test_download_rejected_files_links_the_streamed_zip_of_a_manifest():
    """Refused files listed by a manifest are streamed by the backend, behind
    the user's session."""
    workspace = _rejected_workspace()

    response = _owner_client(workspace).get(_rejected_files_url(workspace))

    assert response.status_code == 200
    assert response.json() == {
        "url": f"http://testserver{_rejected_files_zip_url(workspace)}"
    }


def test_rejected_files_zip_anonymous():
    """Anonymous users must not download refused files."""
    workspace = _rejected_workspace()

    response = APIClient().get(_rejected_files_zip_url(workspace))

    assert response.status_code == 401


def test_rejected_files_zip_other_user_workspace_not_found():
    """A workspace not owned by the requesting user must not be accessible."""
    client = APIClient()
    client.force_login(factories.UserFactory())

    response = client.get(_rejected_files_zip_url(_rejected_workspace()))

    assert response.status_code == 404


def test_rejected_files_zip_requires_a_known_destination():
    """The destination whose refused files are wanted must be registered."""
    workspace = _rejected_workspace()

    response = _owner_client(workspace).get(
        _rejected_files_zip_url(workspace, "unknown")
    )

    assert response.status_code == 400


def test_rejected_files_zip_not_found_without_manifest():
    """A zip of the former export is served by S3 itself, not streamed."""
    workspace = factories.WorkspaceFactory(
        destination_metadata={"drive": {"rejected_files_s3_key": "ws.zip"}}
    )

    response = _owner_client(workspace).get(_rejected_files_zip_url(workspace))

    assert response.status_code == 404


@pytest.mark.usefixtures("rejected_bucket")
def test_rejected_files_zip_streams_the_refused_files():
    """The zip of a destination's refused files is streamed like the archive,
    named after the workspace."""
    workspace = _rejected_workspace(title="Équipe")

    response = _owner_client(workspace).get(_rejected_files_zip_url(workspace))

    assert response.status_code == 200
    content = b"".join(response.streaming_content)
    assert response["Content-Length"] == str(len(content))
    assert response["X-Accel-Buffering"] == "no"
    assert response["Content-Disposition"] == (
        "attachment; filename*=utf-8''%C3%89quipe_fichiers-non-migres_drive.zip"
    )
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert archive.read("docs/big.iso") == b"iso"
        assert archive.read("fichiers-non-migres.csv") == b"csv;"


# ---------------------------------------------------------------------------
# integrity field
# ---------------------------------------------------------------------------


def _run(workspace, task_id, report=None, check_passed=None):
    return ExtraTaskInfo.objects.create(
        task_result=TaskResult.objects.create(task_id=task_id),
        workspace=workspace,
        integrity_report=report or {},
        integrity_check_passed=check_passed,
    )


def _report(migrated, source, sent=None):
    return {
        "summary": {
            "migrated_files_count": migrated,
            "source_files_count": source,
            "sent_files_count": sent,
        },
        "files": [],
    }


def _get_workspace(workspace):
    user = factories.UserFactory()
    user.workspaces.add(workspace)
    client = APIClient()
    client.force_login(user)
    response = client.get(f"/api/v1.0/workspaces/{workspace.id}/")
    assert response.status_code == 200
    return response.json()


def test_workspace_integrity_is_null_without_run():
    """A workspace never migrated has no integrity information."""
    workspace = factories.WorkspaceFactory()

    assert _get_workspace(workspace)["integrity"] is None


def test_workspace_integrity_comes_from_the_latest_run():
    """The file counts and check result of the latest run are exposed."""
    workspace = factories.WorkspaceFactory()
    _run(workspace, "t-1", _report(3, 3), check_passed=True)
    _run(workspace, "t-2", _report(5, 7, sent=6), check_passed=False)

    assert _get_workspace(workspace)["integrity"] == {
        "migrated_files_count": 5,
        "source_files_count": 7,
        "sent_files_count": 6,
        "check_passed": False,
    }


def test_workspace_integrity_sent_count_is_null_without_checked_destination():
    """Without destination checked file by file, the sent count is unknown."""
    workspace = factories.WorkspaceFactory()
    _run(workspace, "t-1", _report(3, 3), check_passed=True)

    assert _get_workspace(workspace)["integrity"]["sent_files_count"] is None


def test_workspace_integrity_is_null_when_latest_run_has_no_report():
    """An older report is not shown for a run migrated without the check."""
    workspace = factories.WorkspaceFactory()
    _run(workspace, "t-1", _report(3, 3), check_passed=True)
    _run(workspace, "t-2")

    assert _get_workspace(workspace)["integrity"] is None


def test_list_workspaces_reads_integrity_without_extra_query_per_workspace(
    django_assert_max_num_queries,
):
    """The integrity information does not add one query per listed workspace."""
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)
    for index in range(3):
        workspace = factories.WorkspaceFactory()
        user.workspaces.add(workspace)
        _run(workspace, f"t-{index}", _report(1, 2), check_passed=True)

    with django_assert_max_num_queries(3):
        response = client.get("/api/v1.0/workspaces/")

    assert response.status_code == 200


def test_list_workspaces_is_ordered_by_status_then_title():
    """Workspaces are listed by status, then alphabetically ignoring case."""
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)
    for title, status in [
        ("beta", "SUCCESS"),
        ("Alpha", "SUCCESS"),
        ("gamma", "FAILURE"),
        ("delta", "NONE"),
    ]:
        user.workspaces.add(factories.WorkspaceFactory(title=title, status=status))

    response = client.get("/api/v1.0/workspaces/")

    assert [w["title"] for w in response.json()["results"]] == [
        "gamma",
        "delta",
        "Alpha",
        "beta",
    ]
