"""Helpers shared by the tests of the export task."""

from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import Ignore

from core.backends.source import SourceFolder
from core.factories import WorkspaceFactory
from core.models import Workspace
from core.processing import tasks
from core.processing.tasks import export

EXPORT_DATA = {"workspace": {"id": "ws-1"}, "user": {"id": "user-1"}}


def export_and_run_tasks(data=None):
    """Run export(), then, as the workers would, each file of its run and its
    finish. Raise the error of the run, if any."""
    with patch("core.processing.tasks.dispatch_batch") as dispatch:
        with pytest.raises(Ignore):
            export(data or EXPORT_DATA)  # pylint: disable=no-value-for-parameter
    run, user = dispatch.call_args.args
    source_backend = tasks.SourceManager().get_backend()
    for migration_file in run.files.iterator():
        if not run.source_token_expired:
            tasks.run_file(run, user, source_backend, migration_file)
    tasks.finish_run(run, user, source_backend)


def make_destination(name):
    """A destination mock named name."""
    destination = MagicMock()
    destination.name = name
    return destination


DRIVE_DOWN = {"drive": RuntimeError("Drive down")}


def saved_workspace(*pending):
    """A saved workspace whose given destinations are pending."""
    return WorkspaceFactory(
        destination_statuses=dict.fromkeys(pending, Workspace.Status.PENDING)
    )


def run_file_by_file_export(
    workspace, user, dest_backends, failures=None, downloaded_path=None
):
    """Run export() with FileExports mocked, the creator downloading every
    row of the run to downloaded_path; return (exports class, creator)."""

    def setup_creator(creator):
        creator.download_file.return_value = downloaded_path

    with patch("core.processing.tasks.FileExports") as exports_cls:
        exports_cls.return_value.close.return_value = failures or {}
        _, creator = run_export_with_creator(
            workspace, user, dest_backends, setup_creator
        )
    return exports_cls, creator


def run_export_with_creator(
    workspace, user, dest_backends, setup_creator=None, setup_source=None
):
    """Run export() for a saved workspace and user, with the creator (and
    source) set up by the caller; return (source backend, creator)."""
    with (
        patch("core.processing.tasks.FolderCreator") as mock_fc,
        patch("core.processing.tasks.SourceManager") as mock_sm,
        patch("core.processing.tasks.DestinationRegistry") as mock_dr,
    ):
        source_backend = mock_sm.return_value.get_backend.return_value
        source_backend.get_workspace_structure.return_value = SourceFolder(name="root")
        if setup_source:
            setup_source(source_backend)
        if setup_creator:
            setup_creator(mock_fc.return_value)
        mock_dr.get_all.return_value = dest_backends

        export_and_run_tasks(
            {"workspace": {"id": workspace.id}, "user": {"id": user.id}}
        )
    return source_backend, mock_fc.return_value
