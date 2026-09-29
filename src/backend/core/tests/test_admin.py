"""Tests for the core admin: UserAdmin actions, Workspace and ExtraTaskInfo pages."""

import csv
import io

from django.contrib import admin
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import Client, RequestFactory
from django.utils import timezone

import pytest
from django_celery_results.models import TaskResult

from core.admin import UserAdmin, WorkspaceAdmin
from core.factories import UserFactory, WorkspaceFactory
from core.models import ExtraTaskInfo, ResanaEmailMapping, User, Workspace

pytestmark = pytest.mark.django_db


def _admin_request():
    """Build a fake admin POST request with a working messages framework."""
    request = RequestFactory().post("/admin/core/user/")
    request.session = {}
    request._messages = FallbackStorage(request)  # pylint: disable=protected-access
    return request


def _connected_user():
    return UserFactory(
        resana_access_token="resana-access",
        resana_refresh_token="resana-refresh",
        resana_session_id="session-id",
        resana_csrf_token="csrf-token",
        resana_token_expires_at=timezone.now(),
        oidc_access_token="drive-access",
        oidc_refresh_token="drive-refresh",
        oidc_token_expires_at=timezone.now(),
    )


def test_reset_resana_connection_clears_resana_tokens_only():
    """reset_resana_connection clears Resana tokens and leaves Drive tokens untouched."""
    user = _connected_user()
    previous_updated_at = user.updated_at
    user_admin = UserAdmin(User, admin.site)

    user_admin.reset_resana_connection(
        _admin_request(), User.objects.filter(pk=user.pk)
    )

    user.refresh_from_db()
    assert user.resana_access_token == ""
    assert user.resana_refresh_token == ""
    assert user.resana_session_id == ""
    assert user.resana_csrf_token == ""
    assert user.resana_token_expires_at is None
    assert user.oidc_access_token == "drive-access"
    assert user.oidc_refresh_token == "drive-refresh"
    assert user.oidc_token_expires_at is not None
    assert user.updated_at > previous_updated_at


def test_reset_drive_connection_clears_drive_tokens_only():
    """reset_drive_connection clears Drive tokens and leaves Resana tokens untouched."""
    user = _connected_user()
    previous_updated_at = user.updated_at
    user_admin = UserAdmin(User, admin.site)

    user_admin.reset_drive_connection(_admin_request(), User.objects.filter(pk=user.pk))

    user.refresh_from_db()
    assert user.oidc_access_token == ""
    assert user.oidc_refresh_token == ""
    assert user.oidc_token_expires_at is None
    assert user.resana_access_token == "resana-access"
    assert user.resana_refresh_token == "resana-refresh"
    assert user.resana_token_expires_at is not None
    assert user.updated_at > previous_updated_at


def test_reset_resana_connection_applies_to_every_selected_user():
    """The action iterates over the whole queryset, not just the first user."""
    users = [_connected_user(), _connected_user()]
    user_admin = UserAdmin(User, admin.site)

    user_admin.reset_resana_connection(
        _admin_request(), User.objects.filter(pk__in=[u.pk for u in users])
    )

    for user in users:
        user.refresh_from_db()
        assert user.resana_access_token == ""


def test_workspace_changelist_renders_with_migration_user():
    """The Workspace changelist must render without crashing on User field lookups."""
    admin_user = UserFactory(is_staff=True, is_superuser=True)
    WorkspaceFactory(migration_user=UserFactory())
    client = Client()
    client.force_login(admin_user)

    response = client.get("/admin/core/workspace/")

    assert response.status_code == 200


def _admin_client():
    client = Client()
    client.force_login(UserFactory(is_staff=True, is_superuser=True))
    return client


def _extra_task(workspace, user=None, task_id="task-1"):
    return ExtraTaskInfo.objects.create(
        task_result=TaskResult.objects.create(task_id=task_id),
        workspace=workspace,
        user=user,
    )


def test_extra_task_changelist_escapes_workspace_title():
    """A workspace title is user data: it is rendered escaped inside its link."""
    workspace = WorkspaceFactory(title="<b>evil</b>")
    _extra_task(workspace, user=UserFactory())

    response = _admin_client().get("/admin/core/extrataskinfo/")

    content = response.content.decode()
    assert f'href="/admin/core/workspace/{workspace.id}/change/"' in content
    assert "&lt;b&gt;evil&lt;/b&gt;" in content
    assert "<b>evil</b>" not in content


def test_extra_task_changelist_links_user_or_shows_none():
    """The user column links to the user, or shows None for a run without user."""
    user = UserFactory()
    _extra_task(WorkspaceFactory(), user=user, task_id="task-1")
    _extra_task(WorkspaceFactory(), user=None, task_id="task-2")

    response = _admin_client().get("/admin/core/extrataskinfo/")

    content = response.content.decode()
    assert f'<a href="/admin/core/user/{user.id}/change/">{user.email}</a>' in content
    assert '<td class="field-get_user">None</td>' in content


def test_workspace_inline_links_task_result():
    """The run inline on the workspace page links to its Celery task result."""
    workspace = WorkspaceFactory()
    extra_task = _extra_task(workspace, user=UserFactory())
    task_result_id = extra_task.task_result.id

    response = _admin_client().get(f"/admin/core/workspace/{workspace.id}/change/")

    assert (
        f'<a href="/admin/django_celery_results/taskresult/{task_result_id}/change/">'
        f"{task_result_id}</a>"
    ) in response.content.decode()


def test_workspace_export_as_csv_writes_one_line_per_workspace():
    """export_as_csv lists user, domain, title, Resana organization, last run date
    and destination statuses, and tolerates a workspace without migration user."""
    ResanaEmailMapping.objects.create(
        domain="example.com",
        resana_organization_name="Org",
        resana_organization_uuid="org-uuid",
    )
    user = UserFactory(email="alice@example.com")
    migrated = WorkspaceFactory(
        title="Migrated",
        migration_user=user,
        destination_statuses={"archive": "SUCCESS", "resana": "FAILURE"},
    )
    # TaskResult.date_done is set automatically on save.
    date_done = _extra_task(migrated, user=user).task_result.date_done
    orphan = WorkspaceFactory(title="Orphan", migration_user=None)
    model_admin = WorkspaceAdmin(Workspace, admin.site)

    response = model_admin.export_as_csv(
        RequestFactory().get("/"),
        Workspace.objects.filter(pk__in=[migrated.pk, orphan.pk]).order_by("title"),
    )

    assert response["Content-Disposition"] == "attachment; filename=core.workspace.csv"
    rows = list(csv.reader(io.StringIO(response.content.decode())))
    assert rows == [
        ["user", "domain", "titre", "destination", "date", "archive", "resana"],
        [
            "alice@example.com",
            "example.com",
            "Migrated",
            "Org",
            str(date_done),
            "SUCCESS",
            "FAILURE",
        ],
        ["", "", "Orphan", "", "", "NONE", "NONE"],
    ]
