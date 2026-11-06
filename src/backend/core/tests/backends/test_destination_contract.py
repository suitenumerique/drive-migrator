"""Tests for the AbstractDestinationBackend contract."""

import pytest

from core.backends.destination import AbstractDestinationBackend, AccountPolicy


class _MinimalDest(AbstractDestinationBackend):
    name = "minimal"
    label = "Minimal Destination"

    def open_export(self, user, run):
        pass

    def put_file(self, user, run, migration_file, local_path):
        pass

    def close_export(self, user, run):
        pass


def test_abstract_destination_cannot_be_instantiated():
    """AbstractDestinationBackend must not be directly instantiable."""
    with pytest.raises(TypeError):
        AbstractDestinationBackend()  # pylint: disable=abstract-class-instantiated


def test_destination_without_the_file_hooks_cannot_be_instantiated():
    """A destination must implement open_export, put_file and close_export."""

    class NoFileHooks(AbstractDestinationBackend):
        name = "no-hooks"
        label = "No Hooks"

    with pytest.raises(TypeError, match="open_export|put_file|close_export"):
        NoFileHooks()  # pylint: disable=abstract-class-instantiated


def test_optional_hooks_raise_not_implemented_by_default():
    """Optional hooks must raise NotImplementedError on the base class."""

    dest = _MinimalDest()
    with pytest.raises(NotImplementedError):
        dest.get_download_url(workspace=None)
    with pytest.raises(NotImplementedError):
        dest.get_error_details(workspace=None)
    with pytest.raises(NotImplementedError):
        dest.retry(workspace=None)
    with pytest.raises(NotImplementedError):
        dest.poll_completion(workspace=None)
    with pytest.raises(NotImplementedError):
        dest.check_integrity(workspace=None, run=None, wait_for_analysis=False)


def test_subclass_without_name_raises():
    """Concrete subclass missing name must raise TypeError at definition time."""
    with pytest.raises(TypeError):

        class BadDest(AbstractDestinationBackend):  # pylint: disable=unused-variable
            label = "Missing Name"

            def open_export(self, user, run):
                pass

            def put_file(self, user, run, migration_file, local_path):
                pass

            def close_export(self, user, run):
                pass


def test_subclass_without_label_raises():
    """Concrete subclass missing label must raise TypeError at definition time."""
    with pytest.raises(TypeError):

        class BadDest(AbstractDestinationBackend):  # pylint: disable=unused-variable
            name = "missing-label"

            def open_export(self, user, run):
                pass

            def put_file(self, user, run, migration_file, local_path):
                pass

            def close_export(self, user, run):
                pass


def test_describe_rejection_defaults_to_a_generic_reason():
    """Without a mapping of its codes, a destination names itself as the refuser."""

    assert (
        _MinimalDest().describe_rejection("any_code")
        == "Fichier refusé par Minimal Destination"
    )


def test_account_hooks_default_to_no_account_required():
    """A destination involves no user account unless it says otherwise."""

    dest = _MinimalDest()
    assert dest.get_account_policy() is AccountPolicy.NONE
    assert dest.user_account_exists(user=None) is True
    assert dest.get_account_creation_url() is None
