"""Shared fixtures for the destination tests."""

import os
import uuid

import pytest

from core.backends.source import SourceFile, SourceFolder
from core.factories import WorkspaceFactory
from core.models import MigrationFile
from core.processing.migration_run import create_migration_run
from core.processing.plan import build_plan


def _folder(paths, dirs):
    """The source tree holding the given file paths and (empty) folder paths."""
    root = SourceFolder(name="root")
    nodes = {"": root}

    def node(path):
        if path not in nodes:
            parent, name = os.path.split(path)
            nodes[path] = SourceFolder(name=name)
            node(parent).children.append(nodes[path])
        return nodes[path]

    for directory in dirs:
        node(directory)
    for path in paths:
        folder, filename = os.path.split(path)
        name, extension = os.path.splitext(filename)
        node(folder).files.append(
            SourceFile(id=path, name=name, extension=extension, download_url="x")
        )
    return root


@pytest.fixture()
def make_run():
    """Build the run of a workspace whose files are all downloaded."""

    def make(paths=("report.pdf",), dirs=(), size=3, **workspace_fields):
        workspace_fields.setdefault("title", "My Workspace")
        run = create_migration_run(
            WorkspaceFactory(**workspace_fields),
            str(uuid.uuid4()),
            build_plan(_folder(paths, dirs)),
        )
        run.files.update(status=MigrationFile.Status.DOWNLOADED, size=size)
        return run

    return make
