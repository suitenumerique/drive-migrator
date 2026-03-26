"""Tests for the AbstractDestinationBackend contract."""

import pytest

from core.backends.destination import AbstractDestinationBackend


def test_abstract_destination_cannot_be_instantiated():
    """AbstractDestinationBackend must not be directly instantiable."""
    with pytest.raises(TypeError):
        AbstractDestinationBackend()


def test_optional_hooks_raise_not_implemented_by_default():
    """Optional hooks must raise NotImplementedError on the base class."""

    class MinimalDest(AbstractDestinationBackend):
        name = "minimal"
        label = "Minimal Destination"

        def export(self, workspace, user, local_folder_path):
            pass

    dest = MinimalDest()
    with pytest.raises(NotImplementedError):
        dest.get_download_url(workspace=None)
    with pytest.raises(NotImplementedError):
        dest.get_error_details(workspace=None)
    with pytest.raises(NotImplementedError):
        dest.retry(workspace=None)
    with pytest.raises(NotImplementedError):
        dest.poll_completion(workspace=None)


def test_subclass_without_name_raises():
    """Concrete subclass missing name must raise TypeError at definition time."""
    with pytest.raises(TypeError):

        class BadDest(AbstractDestinationBackend):
            label = "Missing Name"

            def export(self, workspace, user, local_folder_path):
                pass


def test_subclass_without_label_raises():
    """Concrete subclass missing label must raise TypeError at definition time."""
    with pytest.raises(TypeError):

        class BadDest(AbstractDestinationBackend):
            name = "missing-label"

            def export(self, workspace, user, local_folder_path):
                pass
