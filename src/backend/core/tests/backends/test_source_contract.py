"""Tests for the AbstractSourceBackend contract and source data types."""

import pytest

from core.backends.source import (
    AbstractSourceBackend,
    SourceFile,
    SourceFolder,
    SourceWorkspace,
)
from core.sources.resana.backend import ResanaSourceBackend


def test_abstract_source_cannot_be_instantiated():
    """AbstractSourceBackend must not be directly instantiable."""
    with pytest.raises(TypeError):
        AbstractSourceBackend()


def test_source_workspace_dataclass():
    """SourceWorkspace holds id, title and optional raw_data."""
    ws = SourceWorkspace(id="abc", title="My Workspace")
    assert ws.id == "abc"
    assert ws.title == "My Workspace"
    assert ws.raw_data == {}


def test_source_folder_dataclass():
    """SourceFolder holds a name and empty children/files by default."""
    folder = SourceFolder(name="Docs")
    assert folder.name == "Docs"
    assert not folder.children
    assert not folder.files


def test_source_file_name_with_extension():
    """SourceFile.name_with_extension concatenates name and extension."""
    f = SourceFile(id="1", name="report", extension=".pdf", download_url="/f/1")
    assert f.name_with_extension == "report.pdf"


def test_source_file_name_with_extension_no_ext():
    """SourceFile.name_with_extension works when extension is empty."""
    f = SourceFile(id="2", name="README", extension="", download_url="/f/2")
    assert f.name_with_extension == "README"


def test_prepare_export_default_is_noop():
    """AbstractSourceBackend.prepare_export() must not raise by default."""

    class MinimalSource(AbstractSourceBackend):
        source_type = "minimal"

        def get_workspaces(self, user):
            return []

        def get_workspace_structure(self, workspace):
            return SourceFolder(name="root")

        def download_file(self, file, destination_path):
            pass

    backend = MinimalSource()
    # Must not raise — default implementation is a no-op
    backend.prepare_export(workspace=None, local_folder_path="/tmp")


def test_resana_source_backend_is_concrete_implementation():
    """ResanaSourceBackend is a fully concrete AbstractSourceBackend subclass."""
    assert issubclass(ResanaSourceBackend, AbstractSourceBackend)
    backend = ResanaSourceBackend()
    assert backend.source_type == "resana"


def test_resana_source_type_does_not_collide_with_existing_backends():
    """source_type 'resana' must not collide with osmose or filesystem."""
    assert ResanaSourceBackend.source_type != "osmose"
    assert ResanaSourceBackend.source_type != "filesystem"


def test_subclass_without_source_type_raises():
    """Concrete subclass missing source_type must raise TypeError at definition time."""
    with pytest.raises(TypeError):

        class BadSource(AbstractSourceBackend):  # pylint: disable=unused-variable
            def get_workspaces(self, user):
                return []

            def get_workspace_structure(self, workspace):
                return SourceFolder(name="root")

            def download_file(self, file, destination_path):
                pass
