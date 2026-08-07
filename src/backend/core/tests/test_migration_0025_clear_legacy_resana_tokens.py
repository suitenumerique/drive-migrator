"""Migration 0025 must invalidate tokens issued by the old Resana Auth Service flow."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

import pytest

pytestmark = pytest.mark.django_db(transaction=True)

BEFORE = [("core", "0024_workspace_is_truncated_alter_user_language")]
AFTER = [("core", "0025_user_resana_csrf_token_and_more")]


def test_migration_clears_legacy_resana_tokens():
    """Users connected through the old flow must be forced to reconnect via the bridge."""
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE)
    User = executor.loader.project_state(BEFORE).apps.get_model("core", "User")  # pylint: disable=invalid-name
    legacy = User.objects.create(
        admin_email="legacy@example.com",
        resana_access_token="enc-access",
        resana_refresh_token="enc-refresh",
        resana_token_expires_at=timezone.now(),
    )
    untouched = User.objects.create(admin_email="fresh@example.com")

    executor = MigrationExecutor(connection)
    executor.migrate(AFTER)

    User = executor.loader.project_state(AFTER).apps.get_model("core", "User")  # pylint: disable=invalid-name
    legacy = User.objects.get(pk=legacy.pk)
    assert legacy.resana_access_token == ""
    assert legacy.resana_refresh_token == ""
    assert legacy.resana_token_expires_at is None
    assert legacy.resana_session_id == ""
    assert legacy.resana_csrf_token == ""
    assert User.objects.get(pk=untouched.pk).resana_access_token == ""
