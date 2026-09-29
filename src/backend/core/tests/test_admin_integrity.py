"""Tests for the admin views of the migration integrity report."""

# pylint: disable=redefined-outer-name  # pytest fixtures intentionally shadow outer names

import csv
import io

from django.contrib import admin
from django.test import Client, RequestFactory

import pytest
from django_celery_results.models import TaskResult

from core.admin import ExtraTaskInfoAdmin
from core.factories import UserFactory, WorkspaceFactory
from core.models import ExtraTaskInfo

pytestmark = pytest.mark.django_db


REPORT = {
    "summary": {
        "source_files_count": 2,
        "local_files_count": 2,
        "migrated_files_count": 1,
        "stages": {"ok": 1, "not_ready": 1},
        "destinations_checked": ["drive"],
        "destinations_not_checked": [],
    },
    "files": [
        {
            "source_id": "f1",
            "source_path": "ok.txt",
            "local_path": "ok.txt",
            "destination": "drive",
            "item_id": "item-ok",
            "stage": "ok",
        },
        {
            "source_id": "f2",
            "source_path": "<script>bad</script>.pdf",
            "local_path": "bad.pdf",
            "destination": "drive",
            "item_id": "item-bad",
            "upload_state": "suspicious",
            "stage": "not_ready",
        },
    ],
}


@pytest.fixture()
def admin_client():
    client = Client()
    client.force_login(UserFactory(is_staff=True, is_superuser=True))
    return client


def _extra_task(workspace=None, report=None, check_passed=None, task_id="task-1"):
    return ExtraTaskInfo.objects.create(
        task_result=TaskResult.objects.create(task_id=task_id),
        workspace=workspace or WorkspaceFactory(),
        user=UserFactory(),
        integrity_report=report or {},
        integrity_check_passed=check_passed,
    )


# ---------------------------------------------------------------------------
# WorkspaceAdmin
# ---------------------------------------------------------------------------


def test_workspace_form_labels_unset_tracking_as_following_the_feature_flag(
    admin_client,
):
    """The None choice of the tri-state field reads as following the feature flag."""
    workspace = WorkspaceFactory()

    response = admin_client.get(f"/admin/core/workspace/{workspace.id}/change/")

    assert response.status_code == 200
    assert "Follow the feature flag" in response.content.decode()


def test_workspace_changelist_filters_on_integrity_tracking(admin_client):
    """The changelist can be filtered on the per-workspace tracking override."""
    tracked = WorkspaceFactory(is_file_integrity_tracked=True)
    untracked = WorkspaceFactory(is_file_integrity_tracked=False)

    response = admin_client.get(
        "/admin/core/workspace/?is_file_integrity_tracked__exact=1"
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "By is file integrity tracked" in content
    assert str(tracked.id) in content
    assert str(untracked.id) not in content


def test_workspace_inline_shows_integrity_summary(admin_client):
    """The run inline shows the discrepancy flag and the per-stage counts."""
    workspace = WorkspaceFactory()
    _extra_task(workspace, REPORT, check_passed=False)

    response = admin_client.get(f"/admin/core/workspace/{workspace.id}/change/")

    content = response.content.decode()
    assert "not_ready: 1, ok: 1" in content
    assert "<p>1/2</p>" in content.split('class="field-get_files_migrated"')[1]
    assert "item-bad" not in content


# ---------------------------------------------------------------------------
# ExtraTaskInfoAdmin
# ---------------------------------------------------------------------------


def test_extra_task_changelist_filters_on_failed_integrity_check(admin_client):
    """Runs with an unexplained loss can be listed on their own."""
    suspicious = _extra_task(check_passed=False, task_id="task-bad")
    clean = _extra_task(check_passed=True, task_id="task-ok")

    response = admin_client.get(
        "/admin/core/extrataskinfo/?integrity_check_passed__exact=0"
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "By integrity check passed" in content
    assert f"/admin/core/extrataskinfo/{suspicious.id}/change/" in content
    assert f"/admin/core/extrataskinfo/{clean.id}/change/" not in content


def test_extra_task_changelist_shows_migrated_over_source_files(admin_client):
    """The run list shows migrated/source file counts, "-" without report."""
    with_report = _extra_task(report=REPORT, check_passed=False, task_id="t-1")
    without_report = _extra_task(task_id="t-2")

    response = admin_client.get("/admin/core/extrataskinfo/")

    content = response.content.decode()
    assert "Files migrated" in content
    rows = {
        extra_task.id: content.split(
            f"/admin/core/extrataskinfo/{extra_task.id}/change/"
        )[1]
        for extra_task in (with_report, without_report)
    }
    assert '<td class="field-get_files_migrated">1/2</td>' in rows[with_report.id]
    assert '<td class="field-get_files_migrated">-</td>' in rows[without_report.id]


def test_extra_task_changelist_shows_files_still_under_analysis(admin_client):
    """Files still analyzed by Drive are shown next to the migrated count."""
    report = {
        "summary": {
            "source_files_count": 2,
            "migrated_files_count": 0,
            "stages": {"analysis_unfinished": 2},
        },
        "files": [],
    }
    _extra_task(report=report, check_passed=True)

    response = admin_client.get("/admin/core/extrataskinfo/")

    assert (
        '<td class="field-get_files_migrated">0/2 (2 analyzing)</td>'
        in response.content.decode()
    )


def test_extra_task_change_page_lists_anomalies_first_and_escaped(admin_client):
    """The file table puts anomalies first and escapes user-provided names."""
    extra_task = _extra_task(report=REPORT, check_passed=False)

    response = admin_client.get(f"/admin/core/extrataskinfo/{extra_task.id}/change/")

    assert response.status_code == 200
    content = response.content.decode()
    assert content.index("item-bad") < content.index("item-ok")
    assert "&lt;script&gt;bad&lt;/script&gt;.pdf" in content
    assert "<script>bad</script>" not in content


def _report_with_rows(count):
    return {
        "summary": {**REPORT["summary"], "source_files_count": count},
        "files": [
            {"source_id": f"f{i}", "item_id": f"item-{i:03d}", "stage": "ok"}
            for i in range(count)
        ],
    }


def test_extra_task_change_page_shows_at_most_50_rows(admin_client):
    """Large reports only show their first 50 rows and point to the CSV."""
    extra_task = _extra_task(report=_report_with_rows(60))

    response = admin_client.get(f"/admin/core/extrataskinfo/{extra_task.id}/change/")

    content = response.content.decode()
    assert "item-049" in content
    assert "item-050" not in content
    assert "Showing 50 of 60 rows" in content


def test_extra_task_change_page_links_the_run_csv(admin_client):
    """The detail page links to the CSV export of this run."""
    extra_task = _extra_task(report=REPORT)

    response = admin_client.get(f"/admin/core/extrataskinfo/{extra_task.id}/change/")

    content = response.content.decode()
    assert f'href="/admin/core/extrataskinfo/{extra_task.id}/integrity-csv/"' in content
    assert "Showing" not in content


def test_integrity_csv_view_exports_only_this_run(admin_client):
    """The per-run CSV view exports the rows of that run only."""
    extra_task = _extra_task(report=REPORT, task_id="t-1")
    _extra_task(report=_report_with_rows(3), task_id="t-2")

    response = admin_client.get(
        f"/admin/core/extrataskinfo/{extra_task.id}/integrity-csv/"
    )

    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    rows = list(csv.DictReader(io.StringIO(response.content.decode())))
    assert [row["item_id"] for row in rows] == ["item-ok", "item-bad"]


def test_integrity_csv_view_requires_admin_access():
    """The per-run CSV view is protected like the rest of the admin."""
    extra_task = _extra_task(report=REPORT)
    client = Client()
    client.force_login(UserFactory(is_staff=False))

    response = client.get(f"/admin/core/extrataskinfo/{extra_task.id}/integrity-csv/")

    assert response.status_code == 302
    assert "/admin/login/" in response["Location"]


def test_extra_task_change_page_without_report(admin_client):
    """A run without integrity check renders without a file table."""
    extra_task = _extra_task()

    response = admin_client.get(f"/admin/core/extrataskinfo/{extra_task.id}/change/")

    assert response.status_code == 200
    assert "No integrity report" in response.content.decode()


def test_export_integrity_csv_writes_one_line_per_file_row():
    """The CSV export has one line per report row, prefixed by its task."""
    extra_task = _extra_task(report=REPORT, check_passed=False)
    model_admin = ExtraTaskInfoAdmin(ExtraTaskInfo, admin.site)

    response = model_admin.export_integrity_csv(
        RequestFactory().get("/"), ExtraTaskInfo.objects.filter(pk=extra_task.pk)
    )

    rows = list(csv.DictReader(io.StringIO(response.content.decode())))
    assert [row["item_id"] for row in rows] == ["item-ok", "item-bad"]
    assert rows[1]["task_id"] == "task-1"
    assert rows[1]["source_path"] == "<script>bad</script>.pdf"
    assert rows[1]["upload_state"] == "suspicious"


@pytest.mark.parametrize("object_id", ["999999", "not-an-id"])
def test_integrity_csv_view_unknown_run_is_404(admin_client, object_id):
    """An unknown or malformed run id answers 404, not an empty CSV or a 500."""
    response = admin_client.get(f"/admin/core/extrataskinfo/{object_id}/integrity-csv/")

    assert response.status_code == 404


def test_export_integrity_csv_ignores_unknown_row_keys():
    """A row key missing from REPORT_COLUMNS is dropped instead of failing."""
    report = {**REPORT, "files": [{**REPORT["files"][0], "unexpected": "x"}]}
    extra_task = _extra_task(report=report)
    model_admin = ExtraTaskInfoAdmin(ExtraTaskInfo, admin.site)

    response = model_admin.export_integrity_csv(
        RequestFactory().get("/"), ExtraTaskInfo.objects.filter(pk=extra_task.pk)
    )

    rows = list(csv.DictReader(io.StringIO(response.content.decode())))
    assert [row["item_id"] for row in rows] == ["item-ok"]
    assert "unexpected" not in rows[0]


def test_export_integrity_csv_loads_reports_in_a_single_query(
    django_assert_num_queries,
):
    """The admin queryset defers the report: the export must not load it per run."""
    for index in range(3):
        _extra_task(report=REPORT, task_id=f"t-{index}")
    model_admin = ExtraTaskInfoAdmin(ExtraTaskInfo, admin.site)
    request = RequestFactory().get("/")
    queryset = model_admin.get_queryset(request)

    with django_assert_num_queries(1):
        model_admin.export_integrity_csv(request, queryset)
