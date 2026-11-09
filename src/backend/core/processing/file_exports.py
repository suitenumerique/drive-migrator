"""The destinations of a run, receiving it file by file."""

from contextlib import contextmanager

from django.conf import settings
from django.db import InterfaceError, OperationalError, transaction

from celery.utils.log import get_task_logger

from core.backends.destination import AbstractDestinationBackend, FileExportStatus
from core.models import MigrationFile, MigrationRun, User
from core.processing.migration_run import mark_run_alive

logger = get_task_logger(__name__)


class _Session:
    """A destination along the run, until it closes or fails."""

    def __init__(self, destination: AbstractDestinationBackend):
        self.destination = destination
        self.error = None
        self.consecutive_failures = 0
        self.closed = False
        # Closed by an abort, not by its own closing.
        self.aborted = False
        # Its closing started, maybe cut short with a dead worker.
        self.closing = False

    @property
    def active(self) -> bool:
        return self.error is None and not self.closed

    def merge(self, state: dict) -> None:
        """Take in what the other tasks of the run saved: the failures count,
        and an error or a closing, which are final."""
        self.consecutive_failures = state.get("consecutive_failures", 0)
        self.closed = self.closed or state.get("closed", False)
        self.aborted = self.aborted or state.get("aborted", False)
        self.closing = self.closing or state.get("closing", False)
        if self.error is None and state.get("error") is not None:
            self.error = RuntimeError(state["error"])

    def state(self) -> dict:
        return {
            "error": None if self.error is None else str(self.error),
            "consecutive_failures": self.consecutive_failures,
            "closed": self.closed,
            "aborted": self.aborted,
            "closing": self.closing,
        }

    @property
    def failed(self) -> bool:
        return self.error is not None or self.aborted or not self.closed


class FileExports:
    """
    Hand each downloaded file of a run to its destinations, so that
    a destination failing never stops the others: an error it raises, or too
    many files failing in a row, only disables that destination.

    The state of each destination is saved on the run, so that every task of
    the run builds its own FileExports and carries on from there.
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
        self._merge(run.exports_state)

    @property
    def receiving(self) -> bool:
        """Whether a destination still receives the files of the run."""
        return any(session.active for session in self.sessions)

    def open(self) -> dict[str, Exception]:
        """Open every destination; return the error of each one that failed
        to open by name."""
        for session in self.sessions:
            # Opening may take long: show the watchdog the run goes on.
            mark_run_alive(self.run)
            self._call(
                session,
                session.destination.open_export,
                self.user,
                self.run,
            )
        self._save()
        return {
            session.destination.name: session.error
            for session in self.sessions
            if session.error is not None
        }

    def put(self, migration_file: MigrationFile, local_path: str) -> None:
        """Send one downloaded file to every active destination, then save
        their results on its row in a single UPDATE."""
        # Another task may have disabled a destination meanwhile.
        self._reload()
        # A file task run again does not send its file twice.
        receiving = [
            session
            for session in self.sessions
            if session.active
            and migration_file.results.get(session.destination.name, {}).get("status")
            != FileExportStatus.SENT
        ]
        for session in receiving:
            self._call(
                session,
                session.destination.put_file,
                self.user,
                self.run,
                migration_file,
                local_path,
                # Not the destination's: the file task is retried.
                reraise=(OperationalError, InterfaceError),
            )
        with self._saving():
            for session in receiving:
                self._count_failure(session, migration_file)
            MigrationFile.objects.filter(pk=migration_file.pk).update(
                results=migration_file.results
            )

    def close(self) -> dict[str, Exception]:
        """Close the destinations still active, abort the failed ones, and
        return the error of each failed destination by name."""
        for session in self.sessions:
            if not self._start_closing(session):
                continue
            # Closing may take long: show the watchdog the run goes on.
            mark_run_alive(self.run)
            self._call(
                session,
                session.destination.close_export,
                self.user,
                self.run,
            )
            session.closed = session.error is None
            # At once: a finish taken over later must not abort it.
            self._save()
        self.abort()
        return {
            session.destination.name: session.error
            for session in self.sessions
            if session.error is not None
        }

    def failed(self) -> list[str]:
        """The destinations not closed cleanly, from the state saved by every
        task of the run: in error, aborted or never closed."""
        self._reload()
        return [s.destination.name for s in self.sessions if s.failed]

    def abort(self) -> None:
        """Abort every destination not closed, e.g. after a fatal run error."""
        # A task running alongside may have closed one meanwhile.
        self._reload()
        for session in self.sessions:
            if session.closed:
                continue
            session.closed = session.aborted = True
            try:
                session.destination.abort_export(self.run)
            # Best effort: the first error is the one worth reporting.
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception(
                    "Could not abort the %s export", session.destination.name
                )
        self._save()

    def _start_closing(self, session: _Session) -> bool:
        """Save the closing of an active destination as started. A closing a
        former finish started may have stopped halfway: the destination is
        failed, then aborted, rather than closed twice."""
        with self._saving():
            if not session.active:
                return False
            if session.closing:
                session.error = RuntimeError("Its closing was cut short")
                return False
            session.closing = True
        return True

    def _reload(self) -> None:
        """Take in the state saved by the other tasks of the run."""
        if not self.sessions:
            return
        self._merge(
            MigrationRun.objects.values_list("exports_state", flat=True).get(
                pk=self.run.pk
            )
        )

    def _merge(self, exports_state: dict) -> None:
        for session in self.sessions:
            session.merge(exports_state.get(session.destination.name, {}))

    def _save(self) -> None:
        if not self.sessions:
            return
        with self._saving():
            pass

    @contextmanager
    def _saving(self):
        """Update the sessions from the run's saved state, then save them back,
        the run being locked in between so tasks running alongside queue up."""
        with transaction.atomic():
            exports_state = (
                MigrationRun.objects.select_for_update()
                .values_list("exports_state", flat=True)
                .get(pk=self.run.pk)
            )
            self._merge(exports_state)
            yield
            self.run.exports_state = {
                **exports_state,
                **{
                    session.destination.name: session.state()
                    for session in self.sessions
                },
            }
            MigrationRun.objects.filter(pk=self.run.pk).update(
                exports_state=self.run.exports_state
            )

    def _call(self, session: _Session, method, *args, reraise=()) -> None:
        try:
            method(*args)
        except reraise:
            raise
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
