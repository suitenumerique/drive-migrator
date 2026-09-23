"""Tests for ResanaSourceBackend.begin_export()/finalize_export(): workspace/folder locking.

Split out of test_resana_backend.py to stay under pylint's module line limit.
"""

# pylint: disable=protected-access
import copy
from unittest.mock import MagicMock, patch

from django.db import DatabaseError

import pytest
import requests

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


def _patch_lock_client(mock_cls, top_level_folder_ids=None, folder_access_owners=None):
    mock_cls.return_value.get_connected_user_id.return_value = "2040942"
    mock_cls.return_value.get_top_level_folder_ids.return_value = (
        top_level_folder_ids or []
    )
    mock_cls.return_value.get_folder_access_owners.return_value = folder_access_owners


# ---------------------------------------------------------------------------
# begin_export()
# ---------------------------------------------------------------------------


def test_begin_export_does_nothing_when_slug_not_found(settings):
    """begin_export() must not lock anything if the workspace can't be resolved on the PHP portal."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "Unknown workspace"

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            _patch_members_client(mock_members, slug=None)
            with patch("core.sources.resana.backend.ResanaLockClient") as mock_lock:
                ResanaSourceBackend().begin_export(workspace)

    mock_lock.return_value.lock_workspace.assert_not_called()
    workspace.save.assert_not_called()


def test_begin_export_locks_workspace_when_not_already_locked(settings):
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

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            _patch_members_client(mock_members, slug="2137419", is_locked=False)
            with patch(
                "core.sources.resana.backend.ResanaLockClient", return_value=manager
            ):
                manager.get_top_level_folder_ids.return_value = ["f1", "f2"]
                ResanaSourceBackend().begin_export(workspace)

    assert calls == [("lock", "2137419"), ("grant", "f1"), ("grant", "f2")]


def test_begin_export_does_not_lock_workspace_already_locked(settings):
    """If the workspace is already locked, begin_export() must not lock it again (#215)."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            _patch_members_client(mock_members, slug="2137419", is_locked=True)
            with patch("core.sources.resana.backend.ResanaLockClient") as mock_lock:
                _patch_lock_client(mock_lock)
                ResanaSourceBackend().begin_export(workspace)

    mock_lock.return_value.lock_workspace.assert_not_called()


def test_begin_export_records_workspace_locked_by_us_true_when_we_locked_it(settings):
    """Locking the workspace ourselves is recorded, so finalize_export() unlocks it."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            _patch_members_client(mock_members, slug="2137419", is_locked=False)
            with patch("core.sources.resana.backend.ResanaLockClient") as mock_lock:
                _patch_lock_client(mock_lock)
                ResanaSourceBackend().begin_export(workspace)

    assert workspace.source_lock_state["workspace_locked_by_us"] is True


def test_begin_export_records_workspace_locked_by_us_false_when_already_locked(
    settings,
):
    """A workspace someone else locked is recorded as not ours to unlock."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            _patch_members_client(mock_members, slug="2137419", is_locked=True)
            with patch("core.sources.resana.backend.ResanaLockClient") as mock_lock:
                _patch_lock_client(mock_lock)
                ResanaSourceBackend().begin_export(workspace)

    assert workspace.source_lock_state["workspace_locked_by_us"] is False


def test_begin_export_grants_access_to_our_own_php_user_id(settings):
    """Folder access is granted to the PHP user id the portal session is logged in as."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            _patch_members_client(mock_members, slug="2137419")
            with patch("core.sources.resana.backend.ResanaLockClient") as mock_lock:
                _patch_lock_client(
                    mock_lock, top_level_folder_ids=["f1"], folder_access_owners=None
                )
                ResanaSourceBackend().begin_export(workspace)

    mock_lock.return_value.grant_folder_access.assert_called_once_with(
        "2137419", "f1", "2040942"
    )


def test_begin_export_skips_folder_already_granted_to_us(settings):
    """If a folder is already restricted to exactly our account, don't re-grant it (#215)."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            _patch_members_client(mock_members, slug="2137419")
            with patch("core.sources.resana.backend.ResanaLockClient") as mock_lock:
                _patch_lock_client(
                    mock_lock,
                    top_level_folder_ids=["f1"],
                    folder_access_owners=["2040942"],
                )
                ResanaSourceBackend().begin_export(workspace)

    mock_lock.return_value.grant_folder_access.assert_not_called()


def test_begin_export_records_only_folders_it_actually_granted(settings):
    """folders_granted_by_us must only list folders we actually changed, not all of them."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            _patch_members_client(mock_members, slug="2137419")
            with patch("core.sources.resana.backend.ResanaLockClient") as mock_lock:
                mock_lock.return_value.get_connected_user_id.return_value = "2040942"
                mock_lock.return_value.get_top_level_folder_ids.return_value = [
                    "already-granted",
                    "to-grant",
                ]
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


def test_begin_export_preserves_existing_source_lock_state_keys(settings):
    """Unrelated pre-existing keys in source_lock_state must survive the merge."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"
    workspace.source_lock_state = {"some_future_key": "kept"}

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            _patch_members_client(mock_members, slug="2137419")
            with patch("core.sources.resana.backend.ResanaLockClient") as mock_lock:
                _patch_lock_client(mock_lock)
                ResanaSourceBackend().begin_export(workspace)

    assert workspace.source_lock_state["some_future_key"] == "kept"


def test_begin_export_saves_lock_state_before_granting_folder_access(settings):
    """The lock-state save must happen before any folder is touched (crash-safety)."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"
    calls = []
    workspace.save.side_effect = lambda **kwargs: calls.append("save")

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            _patch_members_client(mock_members, slug="2137419")
            with patch("core.sources.resana.backend.ResanaLockClient") as mock_lock:
                mock_lock.return_value.get_top_level_folder_ids.return_value = ["f1"]
                mock_lock.return_value.get_folder_access_owners.return_value = None
                mock_lock.return_value.grant_folder_access.side_effect = (
                    lambda slug, folder_id, user_id: calls.append("grant")
                )
                ResanaSourceBackend().begin_export(workspace)

    assert calls.index("save") < calls.index("grant")


def test_begin_export_records_slug(settings):
    """The resolved slug is recorded so finalize_export() acts on the same workspace."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            _patch_members_client(mock_members, slug="2137419")
            with patch("core.sources.resana.backend.ResanaLockClient") as mock_lock:
                _patch_lock_client(mock_lock)
                ResanaSourceBackend().begin_export(workspace)

    assert workspace.source_lock_state["slug"] == "2137419"


def test_begin_export_keeps_previous_run_state_after_crash(settings):
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

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            _patch_members_client(mock_members, slug="2137419", is_locked=True)
            with patch("core.sources.resana.backend.ResanaLockClient") as mock_lock:
                mock_lock.return_value.get_connected_user_id.return_value = "2040942"
                mock_lock.return_value.get_top_level_folder_ids.return_value = [
                    "f1",
                    "f2",
                ]
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


def test_begin_export_records_changes_before_remote_calls(settings):
    """Lock and grants are saved as ours before being sent, so a crash right after
    a remote call can't leave a change finalize_export() doesn't know to reverse."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"
    saved_states = []
    workspace.save.side_effect = lambda **kwargs: saved_states.append(
        copy.deepcopy(workspace.source_lock_state)
    )
    state_at_call = {}

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            _patch_members_client(mock_members, slug="2137419", is_locked=False)
            with patch("core.sources.resana.backend.ResanaLockClient") as mock_lock:
                _patch_lock_client(mock_lock, top_level_folder_ids=["f1", "f2"])
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
    assert state_at_call["f1"]["folders_granted_by_us"] == ["f1"]
    assert state_at_call["f2"]["folders_granted_by_us"] == ["f1", "f2"]


def test_begin_export_raises_when_figer_did_not_lock_workspace(settings):
    """figer's 302 can hide a failure (e.g. expired session): the workspace must
    read as locked afterwards, or the migration stops before touching any folder."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            _patch_members_client(mock_members, slug="2137419")
            mock_members.return_value.is_workspace_locked.side_effect = [False, False]
            with patch("core.sources.resana.backend.ResanaLockClient") as mock_lock:
                _patch_lock_client(mock_lock, top_level_folder_ids=["f1"])
                with pytest.raises(ResanaLockError):
                    ResanaSourceBackend().begin_export(workspace)

    mock_lock.return_value.grant_folder_access.assert_not_called()
    # Still recorded as ours, so finalize_export() retries the unlock.
    assert workspace.source_lock_state["workspace_locked_by_us"] is True


# ---------------------------------------------------------------------------
# finalize_export()
# ---------------------------------------------------------------------------


def test_finalize_export_does_nothing_when_no_slug_recorded(settings):
    """Without a slug recorded by begin_export(), there is nothing to reverse."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.source_lock_state = {"workspace_locked_by_us": True}

    with patch("core.sources.resana.backend.ResanaTokenManager"):
        with patch("core.sources.resana.backend.ResanaLockClient") as mock_lock:
            ResanaSourceBackend().finalize_export(workspace)

    mock_lock.assert_not_called()
    workspace.save.assert_not_called()


def test_finalize_export_uses_recorded_slug_without_resolving_it_again(settings):
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

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            with patch(
                "core.sources.resana.backend.ResanaLockClient", return_value=manager
            ):
                ResanaSourceBackend().finalize_export(workspace)

    manager.unlock_workspace.assert_called_once_with("recorded-slug")
    mock_members.return_value.find_slug_by_workspace_uuid.assert_not_called()


def test_finalize_export_does_not_raise_when_lock_client_cannot_be_built(settings):
    """A token refresh failure after a long migration must not raise out of the `finally`."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.source_lock_state = {
        "slug": "2137419",
        "workspace_locked_by_us": True,
        "folders_granted_by_us": ["f1"],
    }

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.side_effect = ResanaTokenExpired("expired")
        with patch("core.sources.resana.backend.ResanaLockClient") as mock_lock:
            ResanaSourceBackend().finalize_export(workspace)  # must not raise

    mock_lock.return_value.unlock_workspace.assert_not_called()
    assert workspace.source_lock_state["folders_granted_by_us"] == ["f1"]


def test_finalize_export_clears_lock_state_after_successful_cleanup(settings):
    """Once released and unlocked, nothing is left recorded as ours to reverse."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.source_lock_state = {
        "slug": "2137419",
        "workspace_locked_by_us": True,
        "folders_granted_by_us": ["f1", "f2"],
    }

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            _patch_members_client(mock_members)
            with patch("core.sources.resana.backend.ResanaLockClient"):
                ResanaSourceBackend().finalize_export(workspace)

    assert workspace.source_lock_state["workspace_locked_by_us"] is False
    assert workspace.source_lock_state["folders_granted_by_us"] == []
    workspace.save.assert_called_once_with(update_fields=["source_lock_state"])


def test_finalize_export_keeps_what_failed_to_be_reversed(settings):
    """Folders not released and a failed unlock stay recorded for a later retry."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.source_lock_state = {
        "slug": "2137419",
        "workspace_locked_by_us": True,
        "folders_granted_by_us": ["f1", "f2"],
    }
    manager = MagicMock()
    manager.release_folder_access.side_effect = [
        None,
        requests.RequestException("boom"),
    ]
    manager.unlock_workspace.side_effect = requests.RequestException("boom")

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient"):
            with patch(
                "core.sources.resana.backend.ResanaLockClient", return_value=manager
            ):
                ResanaSourceBackend().finalize_export(workspace)

    assert workspace.source_lock_state["workspace_locked_by_us"] is True
    assert workspace.source_lock_state["folders_granted_by_us"] == ["f2"]


def test_finalize_export_does_not_raise_when_saving_lock_state_fails(settings):
    """A database error while saving the cleaned-up state must not raise."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.source_lock_state = {
        "slug": "2137419",
        "workspace_locked_by_us": True,
        "folders_granted_by_us": [],
    }
    workspace.save.side_effect = DatabaseError("db down")

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            _patch_members_client(mock_members)
            with patch("core.sources.resana.backend.ResanaLockClient"):
                ResanaSourceBackend().finalize_export(workspace)  # must not raise


def test_finalize_export_unlocks_workspace_when_we_locked_it(settings):
    """Releases our folders first, then unlocks the workspace we locked."""
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

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            _patch_members_client(mock_members, slug="2137419")
            with patch(
                "core.sources.resana.backend.ResanaLockClient", return_value=manager
            ):
                ResanaSourceBackend().finalize_export(workspace)

    assert calls == [("release", "f1"), ("release", "f2"), ("unlock", "2137419")]


def test_finalize_export_does_not_unlock_workspace_we_did_not_lock(settings):
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

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            _patch_members_client(mock_members, slug="2137419")
            with patch(
                "core.sources.resana.backend.ResanaLockClient", return_value=manager
            ):
                ResanaSourceBackend().finalize_export(workspace)

    manager.unlock_workspace.assert_not_called()
    manager.release_folder_access.assert_called_once_with("2137419", "f1")


def test_finalize_export_does_not_unlock_when_lock_state_is_missing(settings):
    """begin_export never having run (empty source_lock_state) must not trigger an unlock."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"
    workspace.source_lock_state = {}
    manager = MagicMock()

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            _patch_members_client(mock_members, slug="2137419")
            with patch(
                "core.sources.resana.backend.ResanaLockClient", return_value=manager
            ):
                ResanaSourceBackend().finalize_export(workspace)

    manager.unlock_workspace.assert_not_called()
    manager.release_folder_access.assert_not_called()


def test_finalize_export_only_releases_folders_recorded_as_granted_by_us(settings):
    """finalize_export() must not re-list top-level folders, only release what was recorded."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"
    workspace.source_lock_state = {
        "slug": "2137419",
        "workspace_locked_by_us": True,
        "folders_granted_by_us": ["f1"],
    }
    manager = MagicMock()

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            _patch_members_client(mock_members, slug="2137419")
            with patch(
                "core.sources.resana.backend.ResanaLockClient", return_value=manager
            ):
                ResanaSourceBackend().finalize_export(workspace)

    manager.release_folder_access.assert_called_once_with("2137419", "f1")
    manager.get_top_level_folder_ids.assert_not_called()


def test_finalize_export_swallows_error_from_one_folder_and_continues(settings):
    """A failure releasing one folder must not prevent releasing the others or unlocking."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.title = "TEST Worskspace"
    workspace.source_lock_state = {
        "slug": "2137419",
        "workspace_locked_by_us": True,
        "folders_granted_by_us": ["f1", "f2"],
    }
    manager = MagicMock()
    manager.release_folder_access.side_effect = [
        requests.RequestException("boom"),
        None,
    ]

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            _patch_members_client(mock_members, slug="2137419")
            with patch(
                "core.sources.resana.backend.ResanaLockClient", return_value=manager
            ):
                ResanaSourceBackend().finalize_export(workspace)  # must not raise

    assert manager.release_folder_access.call_count == 2
    manager.unlock_workspace.assert_called_once_with("2137419")


def test_finalize_export_swallows_error_from_unlock_workspace(settings):
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

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            _patch_members_client(mock_members, slug="2137419")
            with patch(
                "core.sources.resana.backend.ResanaLockClient", return_value=manager
            ):
                ResanaSourceBackend().finalize_export(workspace)  # must not raise


def test_finalize_export_keeps_lock_recorded_when_defiger_did_not_unlock(settings):
    """defiger's 302 can hide a failure: a workspace still locked afterwards stays
    recorded as ours to unlock, without raising."""
    settings.RESANA_WEB_ENDPOINT = "https://resana-web.example.test"
    workspace = _make_workspace()
    workspace.source_lock_state = {
        "slug": "2137419",
        "workspace_locked_by_us": True,
        "folders_granted_by_us": [],
    }

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            _patch_members_client(mock_members, is_locked=True)
            with patch("core.sources.resana.backend.ResanaLockClient") as mock_lock:
                ResanaSourceBackend().finalize_export(workspace)  # must not raise

    mock_lock.return_value.unlock_workspace.assert_called_once_with("2137419")
    assert workspace.source_lock_state["workspace_locked_by_us"] is True


def test_finalize_export_unlocks_based_only_on_recorded_flag_ignoring_concurrent_relock(
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

    with patch("core.sources.resana.backend.ResanaTokenManager") as mock_tm:
        mock_tm.return_value.get_valid_token.return_value = "tok"
        with patch("core.sources.resana.backend.ResanaMembersClient") as mock_members:
            _patch_members_client(mock_members, slug="2137419")
            with patch(
                "core.sources.resana.backend.ResanaLockClient", return_value=manager
            ):
                ResanaSourceBackend().finalize_export(workspace)

    manager.unlock_workspace.assert_called_once_with("2137419")
