"""Tests for the archive of the files a destination refused."""

import csv
import io
import json
from unittest.mock import patch

import pytest

from core.backends.destination import (
    MEMBERS_CSV_FILENAME,
    AbstractDestinationBackend,
    DestinationRegistry,
    RejectedFile,
)
from core.backends.source import SourceFile, SourceFolder
from core.factories import WorkspaceFactory
from core.processing.migration_run import create_migration_run
from core.processing.plan import build_plan
from core.processing.rejected_files import (
    REJECTED_FILES_CSV_NAME,
    RejectedFilesArchive,
    drop_unlinked_kept_files,
    keep_refused_file,
)

pytestmark = pytest.mark.django_db


class FakeDestination(AbstractDestinationBackend):
    """Destination that can refuse files."""

    name = "fake"
    label = "Fake Destination"

    def export(self, workspace, user, local_folder_path):
        pass


class OtherDestination(FakeDestination):
    """Another destination of the same migration."""

    name = "other"
    label = "Other Destination"


@pytest.fixture(autouse=True)
def _destinations(settings):
    """Register the fake destinations, which the failed files CSV looks up."""
    settings.DESTINATION_BACKENDS = [
        f"{__name__}.FakeDestination",
        f"{__name__}.OtherDestination",
    ]
    DestinationRegistry.clear_cache()
    yield
    DestinationRegistry.clear_cache()


@pytest.fixture(name="stored")
def fixture_stored():
    """Capture what is put on S3: {s3_key: bytes}."""
    stored = {}
    with patch(
        "core.processing.rejected_files.ArchiveManager.put_object",
        side_effect=stored.__setitem__,
    ):
        yield stored


@pytest.fixture(name="run")
def fixture_run():
    """A run of three files, the two refused ones kept on S3."""
    files = [
        SourceFile(id="f1", name="big", extension=".iso", download_url="x"),
        SourceFile(id="f2", name="kept", extension=".txt", download_url="x"),
    ]
    sub = SourceFolder(
        name="Hébergement",
        files=[SourceFile(id="f3", name="plan", extension=".exe", download_url="x")],
    )
    plan = build_plan(SourceFolder(name="root", files=files, children=[sub]))
    run = create_migration_run(WorkspaceFactory(), "task-1", plan)
    for path, size in (("big.iso", 3), ("Hébergement/plan.exe", 5)):
        run.files.filter(path=path).update(kept_key=f"kept/{path}", size=size)
    return run


def _manifest(stored, run, destination="fake"):
    key = f"rejected/{run.workspace_id}/{run.id}/{destination}/manifest.json"
    return json.loads(stored[key])


def _build(run, rejected, destination=None):
    RejectedFilesArchive(run.workspace, destination or FakeDestination()).build(
        run, rejected
    )
    run.workspace.refresh_from_db()


def test_build_records_the_rejections_in_upload_errors(run, stored):  # pylint: disable=unused-argument
    """Each refused file is recorded with the destination that refused it."""
    _build(
        run,
        [
            RejectedFile(path="big.iso", code="too_big", item_id="item-1"),
            RejectedFile(path="Hébergement/plan.exe", code="bad_ext"),
        ],
    )

    assert run.workspace.upload_errors == [
        {
            "destination": "fake",
            "path": "big.iso",
            "error": "too_big",
            "item_id": "item-1",
        },
        {"destination": "fake", "path": "Hébergement/plan.exe", "error": "bad_ext"},
    ]


def test_build_keeps_the_rejections_of_other_destinations(run, stored):  # pylint: disable=unused-argument
    """Only the entries of this destination are replaced."""
    other = {"destination": "other", "path": "a.txt", "error": "nope"}
    run.workspace.upload_errors = [
        other,
        {"destination": "fake", "path": "old.txt", "error": "old"},
    ]
    run.workspace.save()

    _build(run, [RejectedFile(path="big.iso", code="too_big")])

    assert run.workspace.upload_errors == [
        other,
        {"destination": "fake", "path": "big.iso", "error": "too_big"},
    ]


def test_build_lists_the_kept_copies_of_the_refused_files(run, stored):
    """The manifest the zip is streamed from lists the refused files at their
    path, from their copy on S3, but not the accepted ones."""
    _build(
        run,
        [
            RejectedFile(path="big.iso", code="too_big"),
            RejectedFile(path="Hébergement/plan.exe", code="bad_ext"),
        ],
    )

    manifest = _manifest(stored, run)
    assert manifest["dirs"] == []
    assert manifest["files"][:2] == [
        {"path": "big.iso", "key": "kept/big.iso", "size": 3},
        {"path": "Hébergement/plan.exe", "key": "kept/Hébergement/plan.exe", "size": 5},
    ]


def test_build_adds_the_failed_files_csv(run, stored):
    """The zip carries the CSV listing every file the migration left behind."""
    run.workspace.download_errors = [
        {"id": "1", "name": "lost.pdf", "path": "lost.pdf", "error": "boom"}
    ]
    run.workspace.save()

    _build(run, [RejectedFile(path="big.iso", code="too_big")])

    entry = _manifest(stored, run)["files"][-1]
    assert REJECTED_FILES_CSV_NAME == "fichiers-non-migres.csv"
    assert entry["path"] == REJECTED_FILES_CSV_NAME
    body = stored[entry["key"]]
    assert entry["size"] == len(body)
    content = body.decode("utf-8-sig")
    paths = [row[0] for row in csv.reader(io.StringIO(content), delimiter=";")]
    assert paths == ["Chemin", "lost.pdf", "big.iso"]


def test_build_leaves_out_the_members_csv(run, stored):
    """The members listing is a migrator file, not one of the user's: a refusal
    of it is neither listed nor archived."""
    _build(run, [RejectedFile(path=MEMBERS_CSV_FILENAME, code="bad_ext")])

    assert run.workspace.upload_errors == []
    assert not stored


def test_build_leaves_out_the_refused_files_not_kept(run, stored):
    """A refused file S3 could not keep is still recorded, but left out of the zip."""
    _build(
        run,
        [
            RejectedFile(path="big.iso", code="too_big"),
            RejectedFile(path="kept.txt", code="too_big"),
        ],
    )

    assert [error["path"] for error in run.workspace.upload_errors] == [
        "big.iso",
        "kept.txt",
    ]
    paths = [entry["path"] for entry in _manifest(stored, run)["files"]]
    assert paths == ["big.iso", REJECTED_FILES_CSV_NAME]


def test_build_stores_the_manifest_key_next_to_the_destination_metadata(run, stored):  # pylint: disable=unused-argument
    """The key is added to the destination metadata, without dropping the rest,
    and replaces a zip of the former export."""
    run.workspace.destination_metadata = {
        "fake": {"root_id": "r-1", "rejected_files_s3_key": "old.zip"}
    }
    run.workspace.save()

    _build(run, [RejectedFile(path="big.iso", code="too_big")])

    assert run.workspace.get_destination_metadata("fake") == {
        "root_id": "r-1",
        "rejected_files_manifest_key": (
            f"rejected/{run.workspace_id}/{run.id}/fake/manifest.json"
        ),
    }


def test_build_without_rejection_stores_nothing(run, stored):
    """No manifest and no key when the destination accepted every file."""
    run.workspace.destination_metadata = {
        "fake": {"root_id": "r-1", "rejected_files_manifest_key": "stale.json"}
    }
    run.workspace.upload_errors = [
        {"destination": "fake", "path": "old.txt", "error": "old"}
    ]
    run.workspace.save()

    _build(run, [])

    assert not stored
    assert run.workspace.upload_errors == []
    assert run.workspace.get_destination_metadata("fake") == {"root_id": "r-1"}


def test_build_still_records_the_rejections_when_s3_fails(run):
    """S3 being down only costs the zip: the migration and its listing go on."""
    with patch(
        "core.processing.rejected_files.ArchiveManager.put_object",
        side_effect=RuntimeError("S3 down"),
    ):
        _build(run, [RejectedFile(path="big.iso", code="too_big")])

    assert run.workspace.upload_errors == [
        {"destination": "fake", "path": "big.iso", "error": "too_big"}
    ]
    assert run.workspace.get_destination_metadata("fake") == {}


@pytest.fixture(name="archive_manager")
def fixture_archive_manager():
    with patch("core.processing.rejected_files.ArchiveManager") as manager_cls:
        yield manager_cls.return_value


def test_clear_deletes_the_zips_of_the_previous_run(archive_manager):
    """Every destination's zip is deleted from S3 and unlinked from the
    workspace, along with the files the previous runs kept."""
    workspace = WorkspaceFactory(
        destination_metadata={
            "fake": {"root_id": "r-1", "rejected_files_s3_key": "fake.zip"},
            "other": {"rejected_files_manifest_key": "rejected/x/manifest.json"},
            "archive": {"s3_key": "workspace.zip"},
        }
    )

    RejectedFilesArchive.clear(workspace)

    archive_manager.delete_file.assert_called_once_with("fake.zip")
    archive_manager.delete_prefix.assert_called_once_with(f"rejected/{workspace.id}")
    workspace.refresh_from_db()
    assert workspace.destination_metadata == {
        "fake": {"root_id": "r-1"},
        "other": {},
        "archive": {"s3_key": "workspace.zip"},
    }


def test_clear_still_unlinks_the_zips_when_s3_fails(archive_manager):
    """S3 being down must not stop a new run from starting."""
    archive_manager.delete_file.side_effect = RuntimeError("S3 down")
    archive_manager.delete_prefix.side_effect = RuntimeError("S3 down")
    workspace = WorkspaceFactory(
        destination_metadata={"fake": {"rejected_files_s3_key": "fake.zip"}}
    )

    RejectedFilesArchive.clear(workspace)

    workspace.refresh_from_db()
    assert workspace.destination_metadata == {"fake": {}}


def test_clear_deletes_the_files_a_run_kept_without_linking_them(archive_manager):
    """A run that stopped before its end kept files that no zip links."""
    workspace = WorkspaceFactory(destination_metadata={"fake": {"root_id": "r-1"}})

    RejectedFilesArchive.clear(workspace)

    archive_manager.delete_file.assert_not_called()
    archive_manager.delete_prefix.assert_called_once_with(f"rejected/{workspace.id}")


def test_drop_unlinked_kept_files_deletes_the_copies_no_zip_lists(run, archive_manager):
    """A destination that failed lists none of the files the run kept for it."""
    drop_unlinked_kept_files(run)

    archive_manager.delete_prefix.assert_called_once_with(
        f"rejected/{run.workspace_id}/{run.id}"
    )


def test_drop_unlinked_kept_files_keeps_the_copies_a_zip_lists(run, archive_manager):
    """The copies stay as long as a destination's zip lists them."""
    run.workspace.set_destination_metadata(
        "fake",
        {
            "rejected_files_manifest_key": (
                f"rejected/{run.workspace_id}/{run.id}/fake/manifest.json"
            )
        },
    )
    run.workspace.save()

    drop_unlinked_kept_files(run)

    archive_manager.delete_prefix.assert_not_called()


def test_drop_unlinked_kept_files_without_copies_calls_no_s3(run, archive_manager):
    """No S3 call for the usual run, where every file was taken."""
    run.files.update(kept_key="")

    drop_unlinked_kept_files(run)

    archive_manager.delete_prefix.assert_not_called()


def test_drop_unlinked_kept_files_swallows_s3_errors(run, archive_manager):
    """It runs as the run ends: S3 being down must not hide how it ended."""
    archive_manager.delete_prefix.side_effect = RuntimeError("S3 down")

    drop_unlinked_kept_files(run)


def test_get_download_url_signs_the_destination_zip():
    """The URL points at this destination's zip, not another one's."""
    workspace = WorkspaceFactory(
        destination_metadata={
            "fake": {"rejected_files_s3_key": "fake.zip"},
            "other": {"rejected_files_s3_key": "other.zip"},
        }
    )

    with patch(
        "core.processing.rejected_files.ArchiveManager.get_presigned_url",
        return_value="http://s3/fake.zip?sig=1",
    ) as get_presigned_url:
        url = RejectedFilesArchive(workspace, FakeDestination()).get_download_url()

    assert url == "http://s3/fake.zip?sig=1"
    get_presigned_url.assert_called_once_with("fake.zip")


def test_get_download_url_is_none_without_zip():
    """No URL when the destination refused nothing."""
    workspace = WorkspaceFactory()

    assert RejectedFilesArchive(workspace, FakeDestination()).get_download_url() is None


def test_keep_refused_file_stores_a_copy_under_the_run(run, tmp_path):
    """A file a destination did not take is kept on S3, so that the user gets
    it back even when the run ends on another worker."""
    local_path = tmp_path / "kept.txt"
    local_path.write_bytes(b"kept")
    migration_file = run.files.get(path="kept.txt")

    with patch(
        "core.processing.rejected_files.ArchiveManager.upload_file"
    ) as upload_file:
        keep_refused_file(run, migration_file, str(local_path))

    key = f"rejected/{run.workspace_id}/{run.id}/files/{migration_file.id}"
    upload_file.assert_called_once_with(str(local_path), key)
    migration_file.refresh_from_db()
    assert migration_file.kept_key == key


def test_keep_refused_file_keeps_no_key_when_s3_fails(run, tmp_path):
    """S3 being down only leaves the file out of the user's zip."""
    local_path = tmp_path / "kept.txt"
    local_path.write_bytes(b"kept")
    migration_file = run.files.get(path="kept.txt")

    with patch(
        "core.processing.rejected_files.ArchiveManager.upload_file",
        side_effect=RuntimeError("S3 down"),
    ):
        keep_refused_file(run, migration_file, str(local_path))

    migration_file.refresh_from_db()
    assert migration_file.kept_key == ""
