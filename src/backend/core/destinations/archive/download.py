"""Archive download: the zip is streamed from the S3 manifest, never stored."""

import json

from django.conf import settings
from django.core.cache import cache
from django.db import connection

from zipstream import ZIP_STORED, ZipStream

from core.destinations.archive.backend import MANIFEST_KEY
from core.processing.folder_helper import ArchiveManager

_SLOTS_KEY = "archive_downloads"
# A worker killed mid-download (e.g. by a redeploy) never releases its slot:
# the counter expires this long after it was created, so leaked slots are
# forgotten, at the cost of under-counting the downloads running by then.
_SLOTS_TIMEOUT = 60 * 60


def get_manifest_key(workspace) -> str | None:
    """S3 key of the workspace's archive manifest, None for a former zip."""
    return workspace.get_destination_metadata("archive").get(MANIFEST_KEY)


def archive_stream(manifest: dict) -> ZipStream:
    """The zip of the manifest's content, each S3 object read once its turn
    comes. Stored, not deflated: the files are often compressed already, and
    the exact size is then known before streaming."""
    stream = ZipStream(compress_type=ZIP_STORED, sized=True)
    manager = ArchiveManager()
    for directory in manifest["dirs"]:
        stream.mkdir(directory)
    for entry in manifest["files"]:
        stream.add(manager.iter_object(entry["key"]), entry["path"], size=entry["size"])
    return stream


def read_manifest(manifest_key: str) -> dict:
    return json.loads(ArchiveManager().read_object(manifest_key))


def acquire_download_slot() -> bool:
    """Take a download slot, or return False when all are taken."""
    if cache.add(_SLOTS_KEY, 1, timeout=_SLOTS_TIMEOUT):
        count = 1
    else:
        try:
            count = cache.incr(_SLOTS_KEY)
        except ValueError:
            # Expired between the two calls.
            cache.add(_SLOTS_KEY, 1, timeout=_SLOTS_TIMEOUT)
            count = 1
    if count > settings.ARCHIVE_MAX_CONCURRENT_DOWNLOADS:
        release_download_slot()
        return False
    return True


def release_download_slot() -> None:
    try:
        if cache.decr(_SLOTS_KEY) < 0:
            cache.set(_SLOTS_KEY, 0, timeout=_SLOTS_TIMEOUT)
    except ValueError:
        # The counter expired: nothing is left to release.
        pass


class SlotStream:
    """Iterate an archive stream for a streaming response, releasing its
    download slot once the response is closed, finished or interrupted."""

    def __init__(self, stream: ZipStream):
        self._stream = stream
        self._chunks = None
        self._released = False

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        if self._chunks is None:
            self._chunks = iter(self._stream)
            # Nothing reads the database while streaming: do not hold its
            # connection for the whole download (it cannot close mid-transaction).
            if not connection.in_atomic_block:
                connection.close()
        return next(self._chunks)

    def close(self) -> None:
        if not self._released:
            self._released = True
            release_download_slot()
