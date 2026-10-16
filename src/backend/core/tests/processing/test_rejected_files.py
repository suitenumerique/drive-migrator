"""Tests for the archive of the files a destination refused."""

import csv
import io
import zipfile
from unittest.mock import patch

import pytest

from core.backends.destination import (
    MEMBERS_CSV_FILENAME,
    AbstractDestinationBackend,
    DestinationRegistry,
    RejectedFile,
)
from core.factories import WorkspaceFactory
from core.processing.rejected_files import (
    REJECTED_FILES_CSV_NAME,
    RejectedFilesArchive,
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


@pytest.fixture(name="uploaded")
def fixture_uploaded():
    """Capture what is sent to S3: {s3_key: {zip entry name: bytes}}."""
    uploaded = {}

    def upload_file(path, s3_key):
        with zipfile.ZipFile(path) as zip_file:
            uploaded[s3_key] = {
                info.filename: zip_file.read(info) for info in zip_file.infolist()
            }

    with patch(
        "core.processing.rejected_files.ArchiveManager.upload_file",
        side_effect=upload_file,
    ):
        yield uploaded


@pytest.fixture(name="local_folder")
def fixture_local_folder(tmp_path):
    """A local workspace folder holding two files, one of them in a subfolder."""
    folder = tmp_path / "workspace"
    (folder / "Hébergement").mkdir(parents=True)
    (folder / "Hébergement" / "plan.exe").write_bytes(b"exe")
    (folder / "big.iso").write_bytes(b"iso")
    (folder / "kept.txt").write_bytes(b"kept")
    return folder


def test_build_records_the_rejections_in_upload_errors(local_folder, uploaded):  # pylint: disable=unused-argument
    """Each refused file is recorded with the destination that refused it."""
    workspace = WorkspaceFactory()

    RejectedFilesArchive(workspace, FakeDestination()).build(
        str(local_folder),
        [
            RejectedFile(path="big.iso", code="too_big", item_id="item-1"),
            RejectedFile(path="Hébergement/plan.exe", code="bad_ext"),
        ],
    )

    workspace.refresh_from_db()
    assert workspace.upload_errors == [
        {
            "destination": "fake",
            "path": "big.iso",
            "error": "too_big",
            "item_id": "item-1",
        },
        {"destination": "fake", "path": "Hébergement/plan.exe", "error": "bad_ext"},
    ]


def test_build_keeps_the_rejections_of_other_destinations(local_folder, uploaded):  # pylint: disable=unused-argument
    """Only the entries of this destination are replaced."""
    other = {"destination": "other", "path": "a.txt", "error": "nope"}
    workspace = WorkspaceFactory(
        upload_errors=[
            other,
            {"destination": "fake", "path": "old.txt", "error": "old"},
        ]
    )

    RejectedFilesArchive(workspace, FakeDestination()).build(
        str(local_folder), [RejectedFile(path="big.iso", code="too_big")]
    )

    workspace.refresh_from_db()
    assert workspace.upload_errors == [
        other,
        {"destination": "fake", "path": "big.iso", "error": "too_big"},
    ]


def test_build_uploads_a_zip_of_the_refused_files(local_folder, uploaded):
    """The zip holds the refused files at their path, but not the accepted ones."""
    workspace = WorkspaceFactory()

    RejectedFilesArchive(workspace, FakeDestination()).build(
        str(local_folder),
        [
            RejectedFile(path="big.iso", code="too_big"),
            RejectedFile(path="Hébergement/plan.exe", code="bad_ext"),
        ],
    )

    key = f"workspace_{workspace.id}_rejected_fake.zip"
    entries = uploaded[key]
    assert entries["big.iso"] == b"iso"
    assert entries["Hébergement/plan.exe"] == b"exe"
    assert "kept.txt" not in entries


def test_build_adds_the_failed_files_csv_to_the_zip(local_folder, uploaded):
    """The zip carries the CSV listing every file the migration left behind."""
    workspace = WorkspaceFactory(
        download_errors=[
            {"id": "1", "name": "lost.pdf", "path": "lost.pdf", "error": "boom"}
        ]
    )

    RejectedFilesArchive(workspace, FakeDestination()).build(
        str(local_folder), [RejectedFile(path="big.iso", code="too_big")]
    )

    entries = uploaded[f"workspace_{workspace.id}_rejected_fake.zip"]
    assert REJECTED_FILES_CSV_NAME == "fichiers-non-migres.csv"
    content = entries[REJECTED_FILES_CSV_NAME].decode("utf-8-sig")
    paths = [row[0] for row in csv.reader(io.StringIO(content), delimiter=";")]
    assert paths == ["Chemin", "lost.pdf", "big.iso"]


def test_build_stores_the_files_without_recompressing(local_folder):
    """Refused files are often big and already compressed (pdf, docx, media):
    deflating them again would only cost time and disk."""
    workspace = WorkspaceFactory()
    compress_types = {}

    def upload_file(path, s3_key):  # pylint: disable=unused-argument
        with zipfile.ZipFile(path) as zip_file:
            compress_types.update(
                {info.filename: info.compress_type for info in zip_file.infolist()}
            )

    with patch(
        "core.processing.rejected_files.ArchiveManager.upload_file",
        side_effect=upload_file,
    ):
        RejectedFilesArchive(workspace, FakeDestination()).build(
            str(local_folder), [RejectedFile(path="big.iso", code="too_big")]
        )

    assert compress_types["big.iso"] == zipfile.ZIP_STORED


def test_build_leaves_out_the_members_csv(local_folder, uploaded):
    """The members listing is a migrator file, not one of the user's: a refusal
    of it is neither listed nor archived."""
    (local_folder / MEMBERS_CSV_FILENAME).write_bytes(b"members")
    workspace = WorkspaceFactory()

    RejectedFilesArchive(workspace, FakeDestination()).build(
        str(local_folder), [RejectedFile(path=MEMBERS_CSV_FILENAME, code="bad_ext")]
    )

    workspace.refresh_from_db()
    assert workspace.upload_errors == []
    assert not uploaded


def test_build_skips_refused_files_missing_locally(local_folder, uploaded):
    """A refused file no longer on disk is still recorded but left out of the zip."""
    workspace = WorkspaceFactory()

    RejectedFilesArchive(workspace, FakeDestination()).build(
        str(local_folder),
        [
            RejectedFile(path="big.iso", code="too_big"),
            RejectedFile(path="gone.txt", code="too_big"),
        ],
    )

    workspace.refresh_from_db()
    assert [error["path"] for error in workspace.upload_errors] == [
        "big.iso",
        "gone.txt",
    ]
    entries = uploaded[f"workspace_{workspace.id}_rejected_fake.zip"]
    assert "gone.txt" not in entries


def test_build_stores_the_s3_key_next_to_the_destination_metadata(
    local_folder,
    uploaded,  # pylint: disable=unused-argument
):
    """The key is added to the destination metadata, without dropping the rest."""
    workspace = WorkspaceFactory(destination_metadata={"fake": {"root_id": "r-1"}})

    RejectedFilesArchive(workspace, FakeDestination()).build(
        str(local_folder), [RejectedFile(path="big.iso", code="too_big")]
    )

    workspace.refresh_from_db()
    assert workspace.get_destination_metadata("fake") == {
        "root_id": "r-1",
        "rejected_files_s3_key": f"workspace_{workspace.id}_rejected_fake.zip",
    }


def test_build_removes_the_local_zip(local_folder, uploaded, settings, tmp_path):  # pylint: disable=unused-argument
    """The zip is only kept on S3, not in the work dir."""
    settings.APP_WORK_DIR = str(tmp_path / "work")
    workspace = WorkspaceFactory()

    RejectedFilesArchive(workspace, FakeDestination()).build(
        str(local_folder), [RejectedFile(path="big.iso", code="too_big")]
    )

    assert not list((tmp_path / "work").glob("*.zip"))


def test_build_without_rejection_uploads_nothing(local_folder, uploaded):
    """No zip and no key when the destination accepted every file."""
    workspace = WorkspaceFactory(
        destination_metadata={
            "fake": {"root_id": "r-1", "rejected_files_s3_key": "stale.zip"}
        },
        upload_errors=[{"destination": "fake", "path": "old.txt", "error": "old"}],
    )

    RejectedFilesArchive(workspace, FakeDestination()).build(str(local_folder), [])

    workspace.refresh_from_db()
    assert not uploaded
    assert workspace.upload_errors == []
    assert workspace.get_destination_metadata("fake") == {"root_id": "r-1"}


def test_build_still_records_the_rejections_when_s3_fails(
    local_folder, settings, tmp_path
):
    """S3 being down only costs the zip: the migration and its listing go on."""
    settings.APP_WORK_DIR = str(tmp_path / "work")
    workspace = WorkspaceFactory()

    with patch(
        "core.processing.rejected_files.ArchiveManager.upload_file",
        side_effect=RuntimeError("S3 down"),
    ):
        RejectedFilesArchive(workspace, FakeDestination()).build(
            str(local_folder), [RejectedFile(path="big.iso", code="too_big")]
        )

    workspace.refresh_from_db()
    assert workspace.upload_errors == [
        {"destination": "fake", "path": "big.iso", "error": "too_big"}
    ]
    assert workspace.get_destination_metadata("fake") == {}
    assert not list((tmp_path / "work").glob("*.zip"))


def test_build_still_records_the_rejections_when_the_work_dir_fails(local_folder):
    """A full or read-only disk only costs the zip, like S3 being down."""
    workspace = WorkspaceFactory()

    with patch(
        "core.processing.rejected_files.os.makedirs",
        side_effect=OSError("Read-only file system"),
    ):
        RejectedFilesArchive(workspace, FakeDestination()).build(
            str(local_folder), [RejectedFile(path="big.iso", code="too_big")]
        )

    workspace.refresh_from_db()
    assert workspace.upload_errors == [
        {"destination": "fake", "path": "big.iso", "error": "too_big"}
    ]
    assert workspace.get_destination_metadata("fake") == {}


def test_clear_deletes_the_zips_of_the_previous_run():
    """Every destination's zip is deleted from S3 and unlinked from the workspace."""
    workspace = WorkspaceFactory(
        destination_metadata={
            "fake": {"root_id": "r-1", "rejected_files_s3_key": "fake.zip"},
            "other": {"rejected_files_s3_key": "other.zip"},
            "archive": {"s3_key": "workspace.zip"},
        }
    )

    with patch(
        "core.processing.rejected_files.ArchiveManager.delete_file"
    ) as delete_file:
        RejectedFilesArchive.clear(workspace)

    assert sorted(call.args[0] for call in delete_file.call_args_list) == [
        "fake.zip",
        "other.zip",
    ]
    workspace.refresh_from_db()
    assert workspace.destination_metadata == {
        "fake": {"root_id": "r-1"},
        "other": {},
        "archive": {"s3_key": "workspace.zip"},
    }


def test_clear_still_unlinks_the_zips_when_s3_fails():
    """S3 being down must not stop a new run from starting."""
    workspace = WorkspaceFactory(
        destination_metadata={"fake": {"rejected_files_s3_key": "fake.zip"}}
    )

    with patch(
        "core.processing.rejected_files.ArchiveManager.delete_file",
        side_effect=RuntimeError("S3 down"),
    ):
        RejectedFilesArchive.clear(workspace)

    workspace.refresh_from_db()
    assert workspace.destination_metadata == {"fake": {}}


def test_clear_without_zip_touches_nothing():
    """No S3 call when no previous run left a zip."""
    workspace = WorkspaceFactory(destination_metadata={"fake": {"root_id": "r-1"}})

    with patch(
        "core.processing.rejected_files.ArchiveManager.delete_file"
    ) as delete_file:
        RejectedFilesArchive.clear(workspace)

    delete_file.assert_not_called()


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
