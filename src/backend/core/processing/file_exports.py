"""Destinations receiving a run file by file, see exports_file_by_file."""

from django.conf import settings

from celery.utils.log import get_task_logger

from core.backends.destination import AbstractDestinationBackend, FileExportStatus
from core.models import MigrationFile, MigrationRun, User

logger = get_task_logger(__name__)


class _Session:
    """A destination along the run, until it closes or fails."""

    def __init__(self, destination: AbstractDestinationBackend):
        self.destination = destination
        self.error = None
        self.consecutive_failures = 0
        self.closed = False

    @property
    def active(self) -> bool:
        return self.error is None and not self.closed


class FileExports:
    """
    Hand each downloaded file of a run to the file-by-file destinations, so that
    a destination failing never stops the others: an error it raises, or too
    many files failing in a row, only disables that destination.
    """

    def __init__(
        self,
        run: MigrationRun,
        user: User,
        destinations: list[AbstractDestinationBackend],
    ):
        self.run = run
        self.user = user
        self.sessions = [_Session(destination) for destination in destinations]

    def open(self) -> None:
        for session in self.sessions:
            self._call(
                session,
                session.destination.open_export,
                self.user,
                self.run,
            )

    def put(self, migration_file: MigrationFile, local_path: str) -> None:
        """Send one downloaded file to every active destination, then save
        their results on its row in a single UPDATE."""
        for session in self.sessions:
            if not session.active:
                continue
            self._call(
                session,
                session.destination.put_file,
                self.user,
                self.run,
                migration_file,
                local_path,
            )
            self._count_failure(session, migration_file)
        MigrationFile.objects.filter(pk=migration_file.pk).update(
            results=migration_file.results
        )

    def close(self) -> dict[str, Exception]:
        """Close the destinations still active, abort the failed ones, and
        return the error of each failed destination by name."""
        for session in self.sessions:
            if session.active:
                self._call(
                    session,
                    session.destination.close_export,
                    self.user,
                    self.run,
                )
                session.closed = session.error is None
        self.abort()
        return {
            session.destination.name: session.error
            for session in self.sessions
            if session.error is not None
        }

    def abort(self) -> None:
        """Abort every destination not closed, e.g. after a fatal run error."""
        for session in self.sessions:
            if session.closed:
                continue
            session.closed = True
            try:
                session.destination.abort_export(self.run)
            # Best effort: the first error is the one worth reporting.
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception(
                    "Could not abort the %s export", session.destination.name
                )

    def _call(self, session: _Session, method, *args) -> None:
        try:
            method(*args)
        # Broad on purpose: whatever a destination raises, it must not stop the
        # others, and the error is reported once the run is over.
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.exception("The %s export failed", session.destination.name)
            session.error = error

    def _count_failure(self, session: _Session, migration_file: MigrationFile) -> None:
        if session.error is not None:
            return
        result = migration_file.results.get(session.destination.name, {})
        if result.get("status") != FileExportStatus.FAILED:
            session.consecutive_failures = 0
            return

        session.consecutive_failures += 1
        limit = settings.MIGRATION_DESTINATION_MAX_CONSECUTIVE_FAILURES
        if session.consecutive_failures >= limit:
            logger.error(
                "%s files in a row failed on %s, disabling it",
                limit,
                session.destination.name,
            )
            session.error = RuntimeError(
                f"{limit} files in a row failed: {result.get('error')}"
            )
