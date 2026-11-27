"""Tests for ResanaSourceBackend.begin_export()/finalize_export(): workspace/folder locking.

Split out of test_resana_backend.py to stay under pylint's module line limit.
"""

# pylint: disable=protected-access
import copy
from unittest.mock import MagicMock

from django.db import DatabaseError

import pytest
import requests

from core.factories import WorkspaceFactory
from core.sources.resana.backend import ResanaSourceBackend
from core.sources.resana.resana_lock_client import ResanaLockError
from core.sources.resana.token_manager import ResanaTokenExpired

pytestmark = pytest.mark.django_db


def _make_workspace(user=None):
    ws = MagicMock()
    ws.source_id = "ws-uuid"
    ws.migration_user = user or MagicMock()
    ws.source_lock_state = {}
    return ws


def _patch_members_client(mock_cls, slug="2137419", members=None, is_locked=False):
    mock_cls.return_value.find_slug_by_workspace_uuid.return_value = slug
    mock_cls.return_value.list_workspace_members.return_value = members or []
    # An unlocked workspace reads as locked once begin_export() has frozen it.
    mock_cls.return_value.is_workspace_locked.side_effect = (
        [True] if is_locked else [False, True]
    )


def _root_folders(folder_ids):
    """Build the list_folders payload for root-only folders."""
    return [
        {"id": folder_id, "name": folder_id, "dossier_mere": None}
        for folder_id in folder_ids
    ]


def _patch_lock_client(mock_cls, top_level_folder_ids=None, folder_access_owners=None):
    mock_cls.return_value.get_connected_user_id.return_value = "2040942"
    mock_cls.return_value.list_folders.return_value = _root_folders(
        top_level_folder_ids or []
    )
    mock_cls.return_value.get_folder_access_owners.return_value = folder_access_owners
    mock_cls.return_value.list_files.return_value = []


_BACKEND = "core.sources.resana.backend"


def _stub_resana_clients(  # noqa: PLR0913  # pylint: disable=too-many-arguments
    monkeypatch,
    *,
    slug="2137419",
    members=None,
    is_locked=False,
    top_level_folder_ids=None,
    folder_access_owners=None,
    stub_members=True,
    stub_lock=True,
    stub_logger=False,
    lock_instance=None,
    configure_lock=True,
):
    """Replace TokenManager / MembersClient / LockClient with MagicMocks."""
    mock_tm = MagicMock()
    mock_tm.return_value.get_valid_token.return_value = "tok"
    monkeypatch.setattr(f"{_BACKEND}.ResanaTokenManager", mock_tm)

    _members = MagicMock()
    if stub_members:
        monkeypatch.setattr(f"{_BACKEND}.ResanaMembersClient", _members)
        _patch_members_client(_members, slug=slug, members=members, is_locked=is_locked)

    mock_lock = MagicMock()
    if stub_lock:
        if lock_instance is not None:
            mock_lock.return_value = lock_instance
        monkeypatch.setattr(f"{_BACKEND}.ResanaLockClient", mock_lock)
        if configure_lock:
            _patch_lock_client(
                mock_lock,
                top_level_folder_ids=top_level_folder_ids,
                folder_access_owners=folder_access_owners,
            )

    mock_logger = MagicMock()
    if stub_logger:
        monkeypatch.setattr(f"{_BACKEND}.logger", mock_logger)

    return mock_tm, _members, mock_lock, mock_logger


# ---------------------------------------------------------------------------
# begin_export()
# ---------------------------------------------------------------------------


def test_begin_export_does_nothing_when_slug_not_found(monkeypatch, settings):
    """begin_export() must not lock anything if the workspace can't be resolved on the PHP portal."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "Unknown workspace"

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch, slug=None, configure_lock=False
    )
    ResanaSourceBackend().begin_export(workspace)

    mock_lock.return_value.lock_workspace.assert_not_called()
    workspace.save.assert_not_called()


def test_begin_export_logs_warning_when_slug_not_found(monkeypatch, settings):
    """An unresolved slug must not be silent: the migration then runs unlocked."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()

    _mock_tm, _members, _lock, mock_logger = _stub_resana_clients(
        monkeypatch, slug=None, stub_logger=True
    )
    ResanaSourceBackend().begin_export(workspace)

    mock_logger.warning.assert_called_once()
    assert "ws-uuid" in mock_logger.warning.call_args[0]


def test_begin_export_locks_workspace_when_not_already_locked(monkeypatch, settings):
    """The workspace must be frozen (figer) before we touch any folder's access."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"
    manager = MagicMock()
    manager.get_folder_access_owners.return_value = None
    calls = []
    manager.lock_workspace.side_effect = lambda slug: calls.append(("lock", slug))
    manager.grant_folder_access.side_effect = (
        lambda slug, folder_id, user_id: calls.append(("grant", folder_id))
    )

    _mock_tm, _members, _lock, _logger = _stub_resana_clients(
        monkeypatch,
        lock_instance=manager,
        configure_lock=False,
        slug="2137419",
        is_locked=False,
    )
    manager.list_folders.return_value = _root_folders(["f1", "f2"])
    ResanaSourceBackend().begin_export(workspace)

    assert calls == [("lock", "2137419"), ("grant", "f1"), ("grant", "f2")]
    manager.release_folder_access.assert_not_called()


def test_begin_export_does_not_lock_workspace_already_locked(monkeypatch, settings):
    """If the workspace is already locked, begin_export() must not lock it again (#215)."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch, is_locked=True
    )
    ResanaSourceBackend().begin_export(workspace)

    mock_lock.return_value.lock_workspace.assert_not_called()


def test_begin_export_records_workspace_locked_by_us_true_when_we_locked_it(
    monkeypatch, settings
):
    """Locking the workspace ourselves is recorded, so finalize_export() unlocks it."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"

    _mock_tm, _members, _lock, _logger = _stub_resana_clients(
        monkeypatch, is_locked=False
    )
    ResanaSourceBackend().begin_export(workspace)

    assert workspace.source_lock_state["workspace_locked_by_us"] is True


def test_begin_export_records_workspace_locked_by_us_false_when_already_locked(
    monkeypatch,
    settings,
):
    """A workspace someone else locked is recorded as not ours to unlock."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"

    _mock_tm, _members, _lock, _logger = _stub_resana_clients(
        monkeypatch, is_locked=True
    )
    ResanaSourceBackend().begin_export(workspace)

    assert workspace.source_lock_state["workspace_locked_by_us"] is False


def test_finalize_export_leaves_folder_grants_in_place_after_unlocking(
    monkeypatch, settings
):
    """Folder saveDroit restrictions stay; only the workspace freeze is reversed."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = WorkspaceFactory(
        source_type="resana",
        source_lock_state={
            "slug": "2137419",
            "workspace_locked_by_us": True,
            "folders_granted_by_us": ["root"],
            "permissions_removed": [{"id": "child", "name": "Secret"}],
        },
    )
    order = []

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch, configure_lock=False
    )
    mock_lock.return_value.release_folder_access.side_effect = (
        lambda _slug, folder_id: order.append(folder_id)
    )
    mock_lock.return_value.unlock_workspace.side_effect = lambda _slug: order.append(
        "unlock"
    )
    ResanaSourceBackend().finalize_export(workspace)

    assert order == ["unlock"]
    mock_lock.return_value.release_folder_access.assert_not_called()
    workspace.refresh_from_db()
    assert workspace.source_lock_state["folders_granted_by_us"] == ["root"]
    assert workspace.source_lock_state["permissions_removed"] == [
        {"id": "child", "name": "Secret"}
    ]


def test_begin_export_grants_access_to_our_own_php_user_id(monkeypatch, settings):
    """Folder access is granted to the PHP user id the portal session is logged in as."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch, configure_lock=False
    )
    _patch_lock_client(
        mock_lock, top_level_folder_ids=["f1"], folder_access_owners=None
    )
    ResanaSourceBackend().begin_export(workspace)

    mock_lock.return_value.grant_folder_access.assert_called_once_with(
        "2137419", "f1", "2040942"
    )


def test_begin_export_skips_folder_already_granted_to_us(monkeypatch, settings):
    """If a folder is already restricted to exactly our account, don't re-grant it (#215)."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch, top_level_folder_ids=["f1"], folder_access_owners=["2040942"]
    )
    ResanaSourceBackend().begin_export(workspace)

    mock_lock.return_value.grant_folder_access.assert_not_called()


def test_begin_export_records_only_folders_it_actually_granted(monkeypatch, settings):
    """folders_granted_by_us must only list folders we actually changed, not all of them."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch, configure_lock=False
    )
    mock_lock.return_value.get_connected_user_id.return_value = "2040942"
    mock_lock.return_value.list_folders.return_value = _root_folders(
        ["already-granted", "to-grant"]
    )
    mock_lock.return_value.get_folder_access_owners.side_effect = (
        lambda slug, folder_id: (
            ["2040942"] if folder_id == "already-granted" else None
        )
    )
    ResanaSourceBackend().begin_export(workspace)

    assert workspace.source_lock_state["folders_granted_by_us"] == ["to-grant"]
    mock_lock.return_value.grant_folder_access.assert_called_once_with(
        "2137419", "to-grant", "2040942"
    )


def test_begin_export_preserves_existing_source_lock_state_keys(monkeypatch, settings):
    """Unrelated pre-existing keys in source_lock_state must survive the merge."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"
    workspace.source_lock_state = {"some_future_key": "kept"}

    _mock_tm, _members, _lock, _logger = _stub_resana_clients(monkeypatch)
    ResanaSourceBackend().begin_export(workspace)

    assert workspace.source_lock_state["some_future_key"] == "kept"


def test_begin_export_saves_lock_state_before_granting_folder_access(
    monkeypatch, settings
):
    """The lock-state save must happen before any folder is touched (crash-safety)."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"
    calls = []
    workspace.save.side_effect = lambda **kwargs: calls.append("save")

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch, configure_lock=False
    )
    mock_lock.return_value.list_folders.return_value = _root_folders(["f1"])
    mock_lock.return_value.get_folder_access_owners.return_value = None
    mock_lock.return_value.grant_folder_access.side_effect = (
        lambda slug, folder_id, user_id: calls.append("grant")
    )
    ResanaSourceBackend().begin_export(workspace)

    assert calls.index("save") < calls.index("grant")


def test_begin_export_records_slug(monkeypatch, settings):
    """The resolved slug is recorded so finalize_export() acts on the same workspace."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"

    _mock_tm, _members, _lock, _logger = _stub_resana_clients(monkeypatch)
    ResanaSourceBackend().begin_export(workspace)

    assert workspace.source_lock_state["slug"] == "2137419"


def test_begin_export_keeps_previous_run_state_after_crash(monkeypatch, settings):
    """A run killed before finalize_export() left the workspace locked and folders
    granted by us: a retry must keep them recorded as ours, not overwrite them."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"
    workspace.source_lock_state = {
        "slug": "2137419",
        "workspace_locked_by_us": True,
        "folders_granted_by_us": ["f1"],
    }

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch, is_locked=True, configure_lock=False
    )
    mock_lock.return_value.get_connected_user_id.return_value = "2040942"
    mock_lock.return_value.list_folders.return_value = _root_folders(["f1", "f2"])
    mock_lock.return_value.get_folder_access_owners.side_effect = (
        lambda slug, folder_id: ["2040942"] if folder_id == "f1" else None
    )
    ResanaSourceBackend().begin_export(workspace)

    mock_lock.return_value.lock_workspace.assert_not_called()
    mock_lock.return_value.grant_folder_access.assert_called_once_with(
        "2137419", "f2", "2040942"
    )
    assert workspace.source_lock_state["workspace_locked_by_us"] is True
    assert workspace.source_lock_state["folders_granted_by_us"] == ["f1", "f2"]


def test_begin_export_records_changes_before_remote_calls(monkeypatch, settings):
    """The workspace lock is saved before figer. A folder grant is saved only
    after saveDroit returns, so a failed grant is not recorded as ours."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"
    saved_states = []
    workspace.save.side_effect = lambda **kwargs: saved_states.append(
        copy.deepcopy(workspace.source_lock_state)
    )
    state_at_call = {}

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch, is_locked=False, top_level_folder_ids=["f1", "f2"]
    )
    mock_lock.return_value.lock_workspace.side_effect = (
        lambda slug: state_at_call.update(lock=saved_states[-1])
    )
    mock_lock.return_value.grant_folder_access.side_effect = (
        lambda slug, folder_id, user_id: state_at_call.update(
            {folder_id: saved_states[-1]}
        )
    )
    ResanaSourceBackend().begin_export(workspace)

    assert state_at_call["lock"]["workspace_locked_by_us"] is True
    assert state_at_call["f1"]["folders_granted_by_us"] == []
    assert state_at_call["f2"]["folders_granted_by_us"] == ["f1"]
    assert workspace.source_lock_state["folders_granted_by_us"] == ["f1", "f2"]


def test_begin_export_grants_again_when_recorded_owners_are_not_us(
    monkeypatch, settings
):
    """A folder id recorded by a previous run is granted again when it is not
    actually limited to our account."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.source_lock_state = {
        "slug": "2137419",
        "workspace_locked_by_us": True,
        "folders_granted_by_us": ["f1"],
    }

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch,
        is_locked=True,
        top_level_folder_ids=["f1"],
        folder_access_owners=["999"],
    )
    ResanaSourceBackend().begin_export(workspace)

    mock_lock.return_value.grant_folder_access.assert_called_once_with(
        "2137419", "f1", "2040942"
    )


def test_begin_export_does_not_regrant_when_recorded_owners_are_us(
    monkeypatch, settings
):
    """A recorded grant whose live owners are already us is left alone."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.source_lock_state = {
        "slug": "2137419",
        "workspace_locked_by_us": True,
        "folders_granted_by_us": ["f1"],
    }

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch,
        is_locked=True,
        top_level_folder_ids=["f1"],
        folder_access_owners=["2040942"],
    )
    ResanaSourceBackend().begin_export(workspace)

    mock_lock.return_value.grant_folder_access.assert_not_called()
    assert workspace.source_lock_state["folders_granted_by_us"] == ["f1"]


def test_begin_export_does_not_record_a_grant_that_raised(monkeypatch, settings):
    """saveDroit raising leaves the folder out of folders_granted_by_us."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch, top_level_folder_ids=["f1"]
    )
    mock_lock.return_value.grant_folder_access.side_effect = ResanaLockError("nope")

    with pytest.raises(ResanaLockError, match="nope"):
        ResanaSourceBackend().begin_export(workspace)

    assert workspace.source_lock_state["folders_granted_by_us"] == []


def test_begin_export_raises_when_figer_did_not_lock_workspace(monkeypatch, settings):
    """figer's 302 can hide a failure (e.g. expired session): the workspace must
    read as locked afterwards, or the migration stops before touching any folder."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()

    _mock_tm, mock_members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch, top_level_folder_ids=["f1"]
    )
    mock_members.return_value.is_workspace_locked.side_effect = [False, False]
    with pytest.raises(ResanaLockError):
        ResanaSourceBackend().begin_export(workspace)

    mock_lock.return_value.grant_folder_access.assert_not_called()
    # Still recorded as ours, so finalize_export() retries the unlock.
    assert workspace.source_lock_state["workspace_locked_by_us"] is True


def test_begin_export_persists_lock_state_in_database(monkeypatch, settings):
    """The recorded lock state must reach the database, since crash recovery relies on it."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = WorkspaceFactory(source_type="resana")

    _mock_tm, _members, _lock, _logger = _stub_resana_clients(
        monkeypatch, is_locked=False, top_level_folder_ids=["f1", "f2"]
    )
    ResanaSourceBackend().begin_export(workspace)

    workspace.refresh_from_db()
    assert workspace.source_lock_state == {
        "slug": "2137419",
        "workspace_locked_by_us": True,
        "folders_granted_by_us": ["f1", "f2"],
    }


# ---------------------------------------------------------------------------
# finalize_export()
# ---------------------------------------------------------------------------


def test_finalize_export_does_nothing_when_no_slug_recorded(monkeypatch, settings):
    """Without a slug recorded by begin_export(), there is nothing to reverse."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.source_lock_state = {"workspace_locked_by_us": True}

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch, stub_members=False, configure_lock=False
    )
    ResanaSourceBackend().finalize_export(workspace)

    mock_lock.assert_not_called()
    workspace.save.assert_not_called()


def test_finalize_export_uses_recorded_slug_without_resolving_it_again(
    monkeypatch, settings
):
    """finalize_export() acts on the slug recorded by begin_export(), not a new lookup."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "Renamed or duplicated title"
    workspace.source_lock_state = {
        "slug": "recorded-slug",
        "workspace_locked_by_us": True,
        "folders_granted_by_us": [],
    }
    manager = MagicMock()

    _mock_tm, mock_members, _lock, _logger = _stub_resana_clients(
        monkeypatch, lock_instance=manager, configure_lock=False
    )
    ResanaSourceBackend().finalize_export(workspace)

    manager.unlock_workspace.assert_called_once_with("recorded-slug")
    mock_members.return_value.find_slug_by_workspace_uuid.assert_not_called()


def test_finalize_export_does_not_raise_when_lock_client_cannot_be_built(
    monkeypatch, settings
):
    """A token refresh failure after a long migration must not raise out of the `finally`."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.source_lock_state = {
        "slug": "2137419",
        "workspace_locked_by_us": True,
        "folders_granted_by_us": ["f1"],
    }

    mock_tm, _members, mock_lock, mock_logger = _stub_resana_clients(
        monkeypatch, stub_members=False, stub_logger=True, configure_lock=False
    )
    mock_tm.return_value.get_token_for_cleanup.side_effect = ResanaTokenExpired(
        "expired"
    )
    ResanaSourceBackend().finalize_export(workspace)  # must not raise

    # The workspace may stay locked: an error, not a warning, with what to reverse by hand.
    mock_logger.error.assert_called_once()
    assert workspace.source_lock_state in mock_logger.error.call_args[0]
    mock_lock.return_value.unlock_workspace.assert_not_called()
    assert workspace.source_lock_state["folders_granted_by_us"] == ["f1"]


def test_finalize_export_clears_lock_flag_after_successful_unlock(
    monkeypatch, settings
):
    """Once unlocked, the freeze flag is cleared; folder grants stay recorded."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.source_lock_state = {
        "slug": "2137419",
        "workspace_locked_by_us": True,
        "folders_granted_by_us": ["f1", "f2"],
    }

    _stub_resana_clients(monkeypatch)
    ResanaSourceBackend().finalize_export(workspace)

    assert workspace.source_lock_state["workspace_locked_by_us"] is False
    assert workspace.source_lock_state["folders_granted_by_us"] == ["f1", "f2"]
    workspace.save.assert_called_once_with(update_fields=["source_lock_state"])


def test_finalize_export_keeps_what_failed_to_be_reversed(monkeypatch, settings):
    """A failed unlock stays recorded for a later retry; folder grants stay."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.source_lock_state = {
        "slug": "2137419",
        "workspace_locked_by_us": True,
        "folders_granted_by_us": ["f1", "f2"],
    }
    manager = MagicMock()
    manager.unlock_workspace.side_effect = requests.RequestException("boom")

    _stub_resana_clients(monkeypatch, lock_instance=manager, configure_lock=False)
    ResanaSourceBackend().finalize_export(workspace)

    assert workspace.source_lock_state["workspace_locked_by_us"] is True
    assert workspace.source_lock_state["folders_granted_by_us"] == ["f1", "f2"]
    manager.release_folder_access.assert_not_called()


def test_finalize_export_does_not_raise_when_saving_lock_state_fails(
    monkeypatch, settings
):
    """A database error while saving the cleaned-up state must not raise."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.source_lock_state = {
        "slug": "2137419",
        "workspace_locked_by_us": True,
        "folders_granted_by_us": [],
    }
    workspace.save.side_effect = DatabaseError("db down")

    _mock_tm, _members, _lock, mock_logger = _stub_resana_clients(
        monkeypatch, stub_logger=True
    )
    ResanaSourceBackend().finalize_export(workspace)  # must not raise

    mock_logger.error.assert_called_once()
    assert workspace.source_lock_state in mock_logger.error.call_args[0]


def test_finalize_export_unlocks_workspace_when_we_locked_it(monkeypatch, settings):
    """Unlocks the workspace we locked, without releasing folder grants."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"
    workspace.source_lock_state = {
        "slug": "2137419",
        "workspace_locked_by_us": True,
        "folders_granted_by_us": ["f1", "f2"],
    }
    manager = MagicMock()
    calls = []
    manager.release_folder_access.side_effect = lambda slug, folder_id: calls.append(
        ("release", folder_id)
    )
    manager.unlock_workspace.side_effect = lambda slug: calls.append(("unlock", slug))

    _mock_tm, _members, _lock, _logger = _stub_resana_clients(
        monkeypatch, lock_instance=manager, configure_lock=False, slug="2137419"
    )
    ResanaSourceBackend().finalize_export(workspace)

    assert calls == [("unlock", "2137419")]
    manager.release_folder_access.assert_not_called()


def test_finalize_export_does_not_unlock_workspace_we_did_not_lock(
    monkeypatch, settings
):
    """If we didn't lock the workspace ourselves, finalize_export() must not unlock it (#215)."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"
    workspace.source_lock_state = {
        "slug": "2137419",
        "workspace_locked_by_us": False,
        "folders_granted_by_us": ["f1"],
    }
    manager = MagicMock()

    _mock_tm, _members, _lock, _logger = _stub_resana_clients(
        monkeypatch, lock_instance=manager, configure_lock=False, slug="2137419"
    )
    ResanaSourceBackend().finalize_export(workspace)

    manager.unlock_workspace.assert_not_called()
    manager.release_folder_access.assert_not_called()


def test_finalize_export_does_not_unlock_when_lock_state_is_missing(
    monkeypatch, settings
):
    """begin_export never having run (empty source_lock_state) must not trigger an unlock."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"
    workspace.source_lock_state = {}
    manager = MagicMock()

    _mock_tm, _members, _lock, _logger = _stub_resana_clients(
        monkeypatch, lock_instance=manager, configure_lock=False, slug="2137419"
    )
    ResanaSourceBackend().finalize_export(workspace)

    manager.unlock_workspace.assert_not_called()
    manager.release_folder_access.assert_not_called()


def test_finalize_export_does_not_release_or_list_folders(monkeypatch, settings):
    """finalize_export() must not list or release folders, only unlock."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"
    workspace.source_lock_state = {
        "slug": "2137419",
        "workspace_locked_by_us": True,
        "folders_granted_by_us": ["f1"],
    }
    manager = MagicMock()

    _mock_tm, _members, _lock, _logger = _stub_resana_clients(
        monkeypatch, lock_instance=manager, configure_lock=False, slug="2137419"
    )
    ResanaSourceBackend().finalize_export(workspace)

    manager.release_folder_access.assert_not_called()
    manager.list_folders.assert_not_called()
    manager.unlock_workspace.assert_called_once_with("2137419")


def test_finalize_export_swallows_error_from_unlock_workspace(monkeypatch, settings):
    """A failure unlocking the workspace must not raise out of finalize_export."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"
    workspace.source_lock_state = {
        "slug": "2137419",
        "workspace_locked_by_us": True,
        "folders_granted_by_us": [],
    }
    manager = MagicMock()
    manager.unlock_workspace.side_effect = requests.RequestException("boom")

    _mock_tm, _members, _lock, mock_logger = _stub_resana_clients(
        monkeypatch, lock_instance=manager, configure_lock=False, stub_logger=True
    )
    ResanaSourceBackend().finalize_export(workspace)  # must not raise

    # The workspace stays frozen for all its members: an error, not a warning.
    mock_logger.error.assert_called_once()
    assert "2137419" in mock_logger.error.call_args[0]


def test_finalize_export_keeps_lock_recorded_when_defiger_did_not_unlock(
    monkeypatch, settings
):
    """defiger's 302 can hide a failure: a workspace still locked afterwards stays
    recorded as ours to unlock, without raising."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.source_lock_state = {
        "slug": "2137419",
        "workspace_locked_by_us": True,
        "folders_granted_by_us": [],
    }

    _mock_tm, _members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch, is_locked=True, configure_lock=False
    )
    ResanaSourceBackend().finalize_export(workspace)  # must not raise

    mock_lock.return_value.unlock_workspace.assert_called_once_with("2137419")
    assert workspace.source_lock_state["workspace_locked_by_us"] is True


def test_finalize_export_unlocks_based_only_on_recorded_flag_ignoring_concurrent_relock(
    monkeypatch,
    settings,
):
    """Known limitation: finalize_export trusts its recorded flag, with no live
    check before unlocking: it can't distinguish "still locked because it's us" from "someone
    else re-locked it independently in the meantime" (#215)."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"
    workspace.source_lock_state = {
        "slug": "2137419",
        "workspace_locked_by_us": True,
        "folders_granted_by_us": [],
    }
    manager = MagicMock()

    _mock_tm, _members, _lock, _logger = _stub_resana_clients(
        monkeypatch, lock_instance=manager, configure_lock=False, slug="2137419"
    )
    ResanaSourceBackend().finalize_export(workspace)

    manager.unlock_workspace.assert_called_once_with("2137419")


def test_finalize_export_persists_remaining_lock_state_in_database(
    monkeypatch, settings
):
    """A failed unlock must reach the database, so a later retry can reverse it."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = WorkspaceFactory(
        source_type="resana",
        source_lock_state={
            "slug": "2137419",
            "workspace_locked_by_us": True,
            "folders_granted_by_us": ["f1", "f2"],
        },
    )

    _stub_resana_clients(monkeypatch, is_locked=True)
    ResanaSourceBackend().finalize_export(workspace)

    workspace.refresh_from_db()
    assert workspace.source_lock_state == {
        "slug": "2137419",
        "workspace_locked_by_us": True,
        "folders_granted_by_us": ["f1", "f2"],
    }


def test_begin_export_locks_nothing_when_the_offline_token_is_no_longer_valid(
    monkeypatch,
    settings,
):
    """A dead offline token fails the task before the workspace is locked,
    instead of a few hours later with the workspace left locked."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()

    mock_tm, _members, mock_lock, _logger = _stub_resana_clients(monkeypatch)
    mock_tm.return_value.ensure_offline_token.side_effect = ResanaTokenExpired(
        "Resana offline token is no longer valid."
    )
    with pytest.raises(ResanaTokenExpired):
        ResanaSourceBackend().begin_export(workspace)

    mock_tm.assert_called_with(workspace.migration_user)
    mock_lock.return_value.lock_workspace.assert_not_called()
    workspace.save.assert_not_called()


def test_finalize_export_unlocks_with_the_cleanup_token(monkeypatch, settings):
    """finalize_export() builds its clients from the cleanup token, so it can
    still unlock after the run stopped on a refused refresh."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.source_lock_state = {
        "slug": "2137419",
        "workspace_locked_by_us": True,
        "folders_granted_by_us": [],
    }

    mock_tm, mock_members, mock_lock, _logger = _stub_resana_clients(
        monkeypatch, is_locked=True
    )
    mock_tm.return_value.get_valid_token.side_effect = ResanaTokenExpired()
    mock_tm.return_value.get_token_for_cleanup.return_value = "last-minute"
    ResanaSourceBackend().finalize_export(workspace)

    assert mock_lock.call_args.kwargs["access_token"] == "last-minute"
    assert mock_members.call_args.kwargs["access_token"] == "last-minute"
    mock_lock.return_value.unlock_workspace.assert_called_once_with("2137419")
    # Fetched once: a second Keycloak timeout would outlast the last minute.
    mock_tm.return_value.get_token_for_cleanup.assert_called_once()
