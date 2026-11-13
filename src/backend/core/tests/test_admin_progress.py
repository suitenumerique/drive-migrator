"""Tests for the progress of the running migrations in the Django admin."""

from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

import pytest
from django_celery_results.models import TaskResult

from core.backends.source import SourceFile, SourceFolder
from core.factories import UserFactory, WorkspaceFactory
from core.models import ExtraTaskInfo, MigrationRun
from core.processing.migration_run import create_migration_run
from core.processing.plan import build_plan

pytestmark = pytest.mark.django_db


@pytest.fixture(name="admin_client")
def fixture_admin_client():
    client = Client()
    client.force_login(UserFactory(is_staff=True, is_superuser=True))
    return client


def _migration(task_id, workspace=None, files_count=None, files_done=0):
    """A migration, running with files_count files of which files_done are
    done; without a run when files_count is None."""
    workspace = workspace or WorkspaceFactory()
    extra_task = ExtraTaskInfo.objects.create(
        task_result=TaskResult.objects.create(task_id=task_id),
        workspace=workspace,
        user=UserFactory(),
    )
    if files_count is not None:
        files = [
            SourceFile(id=f"f{i}", name=f"doc{i}", extension=".txt", download_url="x")
            for i in range(files_count)
        ]
        plan = build_plan(SourceFolder(name="root", files=files))
        run = create_migration_run(workspace, task_id, plan)
        MigrationRun.objects.filter(pk=run.pk).update(
            remaining_files=files_count - files_done
        )
    return extra_task


def _row(content, extra_task):
    return content.split(f"/admin/core/extrataskinfo/{extra_task.id}/change/")[1]


def test_extra_task_changelist_shows_the_progress_of_running_migrations(
    admin_client,
):
    """A running migration shows its files done over its files; "-" once its
    run is purged."""
    running = _migration("t-1", files_count=5, files_done=2)
    finished = _migration("t-2")

    response = admin_client.get("/admin/core/extrataskinfo/")

    content = response.content.decode()
    assert "Progress" in content
    assert '<td class="field-get_progress">2/5</td>' in _row(content, running)
    assert '<td class="field-get_progress">-</td>' in _row(content, finished)


def test_workspace_inline_shows_the_progress_of_its_migrations(admin_client):
    """The migrations listed on a workspace show their progress too."""
    workspace = WorkspaceFactory()
    _migration("t-1", workspace=workspace, files_count=4, files_done=3)

    response = admin_client.get(f"/admin/core/workspace/{workspace.id}/change/")

    content = response.content.decode()
    assert "<p>3/4</p>" in content.split('class="field-get_progress"')[1]


def test_extra_task_changelist_reads_the_progress_in_a_constant_number_of_queries(
    admin_client,
):
    """The progress of every migration of the page comes with the page query."""

    def count_queries():
        with CaptureQueriesContext(connection) as queries:
            admin_client.get("/admin/core/extrataskinfo/")
        return len(queries)

    _migration("t-1", files_count=2)
    one_migration = count_queries()
    _migration("t-2", files_count=2)
    _migration("t-3", files_count=2)

    assert count_queries() == one_migration
