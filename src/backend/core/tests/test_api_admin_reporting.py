"""Tests for the organization admin reporting API."""

from django.utils import timezone

import pytest
from django_celery_results.models import TaskResult
from rest_framework.test import APIClient

from core import factories
from core.admin_reporting import organization_stats
from core.models import ExtraTaskInfo, FeatureFlag, Workspace

pytestmark = pytest.mark.django_db

CONTEXT = "/api/v1.0/admin/context"
WORKSPACES = "/api/v1.0/admin/workspaces"


def _flag(active):
    FeatureFlag.objects.update_or_create(
        name=FeatureFlag.Name.ADMIN_REPORTING,
        defaults={"is_active": active},
    )


def _client():
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)
    return client, user


def _run(workspace, task_id, status="SUCCESS", report=None, check_passed=None):
    return ExtraTaskInfo.objects.create(
        workspace=workspace,
        task_result=TaskResult.objects.create(
            task_id=task_id,
            status=status,
            date_done=timezone.now(),
        ),
        integrity_report=report or {},
        integrity_check_passed=check_passed,
    )


@pytest.fixture
def feature_enabled():
    """Turn the admin reporting flag on. No Resana session is involved."""
    _flag(True)


def reporting(test):
    """Apply the feature flag fixture without a pylint-visible argument."""
    return pytest.mark.usefixtures("feature_enabled")(test)


def test_context_anonymous():
    """Anonymous users are refused before the flag is considered."""
    response = APIClient().get(CONTEXT)
    assert response.status_code == 401


def test_context_flag_off():
    """An inactive flag refuses the route."""
    _flag(False)
    client, _user = _client()
    response = client.get(CONTEXT)
    assert response.status_code == 403
    assert response.json()["error_name"] == "FeatureNotEnabled"


def test_context_flag_missing():
    """A missing flag row stays disabled, unlike is_feature()."""
    FeatureFlag.objects.filter(name=FeatureFlag.Name.ADMIN_REPORTING).delete()
    client, _user = _client()
    response = client.get(CONTEXT)
    assert response.status_code == 403
    assert response.json()["error_name"] == "FeatureNotEnabled"


@reporting
def test_context_without_organization():
    """No administered organization means the non-admin screen, without calling Resana."""
    client, user = _client()
    response = client.get(CONTEXT)
    assert response.status_code == 200
    assert response.json() == {
        "is_organization_admin": False,
        "features": {"admin_reporting": True},
        "user": {"name": None, "email": user.email},
        "organizations": [],
    }


@reporting
def test_context_lists_administered_organizations():
    """Only organizations the user administers are listed, by name."""
    client, user = _client()
    synced = timezone.now()
    alpha = factories.OrganizationFactory(
        name="Alpha",
        source_id="org-alpha",
        can_backup=True,
        last_sync=synced,
    )
    beta = factories.OrganizationFactory(
        name="Beta",
        source_id="org-beta",
        can_backup=False,
    )
    factories.OrganizationFactory(name="Gamma", source_id="org-gamma")
    alpha.admins.add(user)
    beta.admins.add(user)

    response = client.get(CONTEXT)

    assert response.status_code == 200
    body = response.json()
    assert body["is_organization_admin"] is True
    assert [org["id"] for org in body["organizations"]] == ["org-alpha", "org-beta"]
    assert body["organizations"][0]["can_backup"] is True
    assert body["organizations"][0]["name"] == "Alpha"
    assert body["organizations"][0]["last_sync"] is not None
    assert body["organizations"][1]["last_sync"] is None
    assert body["organizations"][1]["can_backup"] is False


@reporting
def test_workspaces_exclude_other_organizations():
    """The list is the chosen organization only, including its eligible count."""
    client, user = _client()
    org = factories.OrganizationFactory(source_id="mine")
    other = factories.OrganizationFactory(source_id="other")
    org.admins.add(user)
    factories.WorkspaceFactory(organization=org, title="Mine")
    factories.WorkspaceFactory(organization=other, title="Other")
    factories.WorkspaceFactory(title="Loose")

    response = client.get(WORKSPACES, {"organization": "mine"})

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["eligible_count"] == 1
    assert [row["title"] for row in body["results"]] == ["Mine"]


@reporting
def test_workspaces_hides_unknown_and_foreign_organizations():
    """Unknown and non-administered organizations answer the same refusal."""
    client, _user = _client()
    factories.OrganizationFactory(source_id="secret")

    foreign = client.get(WORKSPACES, {"organization": "secret"})
    missing = client.get(WORKSPACES, {"organization": "missing"})
    absent = client.get(WORKSPACES)

    assert foreign.json()["error_name"] == "NotOrganizationAdmin"
    assert missing.json()["error_name"] == "NotOrganizationAdmin"
    assert absent.json()["error_name"] == "InvalidQueryParam"


@reporting
def test_workspaces_pagination():
    """page_size follows the contract and does not use the global page of 1000."""
    client, user = _client()
    org = factories.OrganizationFactory()
    org.admins.add(user)
    for index in range(26):
        factories.WorkspaceFactory(organization=org, title=f"Space {index:02d}")

    first = client.get(
        WORKSPACES, {"organization": org.source_id, "page_size": "25"}
    ).json()
    second = client.get(
        WORKSPACES,
        {"organization": org.source_id, "page_size": "25", "page": "2"},
    ).json()

    assert first["count"] == 26
    assert len(first["results"]) == 25
    assert first["next"]
    assert len(second["results"]) == 1
    assert second["previous"]


@reporting
def test_workspaces_reject_bad_query_params():
    """page_size, ordering and filters outside the contract are refused."""
    client, user = _client()
    org = factories.OrganizationFactory()
    org.admins.add(user)

    for params in (
        {"page_size": "10"},
        {"ordering": "target"},
        {"status": "SUCCESS"},
        {"connected": "maybe"},
    ):
        response = client.get(WORKSPACES, {"organization": org.source_id, **params})
        assert response.status_code == 403
        assert response.json()["error_name"] == "InvalidQueryParam"


@reporting
def test_eligible_count_ignores_migrated_and_pending():
    """NONE and FAILURE can be launched. SUCCESS and PENDING cannot."""
    client, user = _client()
    org = factories.OrganizationFactory()
    org.admins.add(user)
    factories.WorkspaceFactory(organization=org, title="Todo")
    failed = factories.WorkspaceFactory(organization=org, title="Failed")
    failed.set_destination_status("drive", Workspace.Status.FAILURE)
    failed.save()
    done = factories.WorkspaceFactory(organization=org, title="Done")
    done.set_destination_status("drive", Workspace.Status.SUCCESS)
    done.save()
    pending = factories.WorkspaceFactory(organization=org, title="Pending")
    pending.set_destination_status("drive", Workspace.Status.PENDING)
    pending.save()

    everything = client.get(WORKSPACES, {"organization": org.source_id}).json()
    assert everything["count"] == 4
    assert everything["eligible_count"] == 2

    not_migrated = client.get(
        WORKSPACES, {"organization": org.source_id, "status": "not_migrated"}
    ).json()
    assert not_migrated["count"] == 2
    assert not_migrated["eligible_count"] == 2
    assert {row["title"] for row in not_migrated["results"]} == {"Todo", "Failed"}

    migrated = client.get(
        WORKSPACES, {"organization": org.source_id, "status": "migrated"}
    ).json()
    assert migrated["count"] == 1
    assert migrated["eligible_count"] == 0
    assert migrated["results"][0]["target"] == "drive"
    assert migrated["results"][0]["status"] == "SUCCESS"


@reporting
def test_gap_from_refused_files():
    """Refused files turn a successful migration into GAP."""
    client, user = _client()
    org = factories.OrganizationFactory()
    org.admins.add(user)
    workspace = factories.WorkspaceFactory(organization=org, title="Avec écart")
    workspace.set_destination_status("drive", Workspace.Status.SUCCESS)
    workspace.upload_errors = [
        {"destination": "drive", "path": "a.txt", "error": "rejected"}
    ]
    workspace.save()

    body = client.get(
        WORKSPACES, {"organization": org.source_id, "status": "GAP"}
    ).json()

    assert body["count"] == 1
    assert body["eligible_count"] == 0
    assert body["results"][0]["status"] == "GAP"
    assert body["results"][0]["target"] == "drive"


@reporting
def test_gap_from_integrity_exposes_counts_and_origin():
    """A failed integrity check is GAP and exposes the latest successful run."""
    client, user = _client()
    org = factories.OrganizationFactory()
    org.admins.add(user)
    workspace = factories.WorkspaceFactory(organization=org, title="Contrôle")
    workspace.set_destination_status("archive", Workspace.Status.SUCCESS)
    workspace.save()
    _run(
        workspace,
        "gap-1",
        check_passed=False,
        report={
            "summary": {
                "source_files_count": 4,
                "migrated_files_count": 3,
                "sent_files_count": 3,
            },
            "files": [],
        },
    )

    row = client.get(WORKSPACES, {"organization": org.source_id}).json()["results"][0]

    assert row["status"] == "GAP"
    assert row["target"] == "archive"
    assert row["origin"] == "manager"
    assert row["source_files_count"] == 4
    assert row["migrated_files_count"] == 3
    assert row["migrated_at"] is not None
    assert row["queue"] is None


@reporting
def test_file_counts_coerce_report_strings_to_integers():
    """A count stored as a JSON string becomes an integer. No report stays null."""
    client, user = _client()
    org = factories.OrganizationFactory()
    org.admins.add(user)
    counted = factories.WorkspaceFactory(organization=org, title="Compté")
    _run(
        counted,
        "counts-1",
        report={
            "summary": {
                "source_files_count": "12",
                "migrated_files_count": "7",
            },
            "files": [],
        },
    )
    factories.WorkspaceFactory(organization=org, title="Sans rapport")

    rows = {
        row["title"]: row
        for row in client.get(WORKSPACES, {"organization": org.source_id}).json()[
            "results"
        ]
    }

    assert rows["Compté"]["source_files_count"] == 12
    assert rows["Compté"]["migrated_files_count"] == 7
    assert rows["Sans rapport"]["source_files_count"] is None
    assert rows["Sans rapport"]["migrated_files_count"] is None


@reporting
def test_search_ignores_accents_and_matches_managers():
    """Search matches a title without its accents, and a manager's full name."""
    client, user = _client()
    org = factories.OrganizationFactory()
    org.admins.add(user)
    factories.WorkspaceFactory(organization=org, title="Comité été")
    factories.WorkspaceFactory(organization=org, title="Budget")
    factories.WorkspaceFactory(
        organization=org,
        title="Autre",
        members=[
            {
                "name": "Martin",
                "firstName": "Claire",
                "email": "claire.martin@dgad.gouv.fr",
            }
        ],
    )

    by_title = client.get(
        WORKSPACES, {"organization": org.source_id, "search": "ete"}
    ).json()
    by_name = client.get(
        WORKSPACES, {"organization": org.source_id, "search": "Claire Martin"}
    ).json()

    assert [row["title"] for row in by_title["results"]] == ["Comité été"]
    assert [row["title"] for row in by_name["results"]] == ["Autre"]


@reporting
def test_connected_count_matches_a_linked_user():
    """A manager is connected when a user with the same email holds the space."""
    client, user = _client()
    org = factories.OrganizationFactory()
    org.admins.add(user)
    workspace = factories.WorkspaceFactory(
        organization=org,
        members=[
            {
                "name": "Martin",
                "firstName": "Claire",
                "email": "Claire.Martin@dgad.gouv.fr",
            },
            {
                "name": "Roux",
                "firstName": "Paul",
                "email": "paul.roux@dgad.gouv.fr",
            },
        ],
    )
    manager = factories.UserFactory(email="claire.martin@dgad.gouv.fr")
    manager.workspaces.add(workspace)

    row = client.get(WORKSPACES, {"organization": org.source_id}).json()["results"][0]
    connected = client.get(
        WORKSPACES, {"organization": org.source_id, "connected": "yes"}
    ).json()
    disconnected = client.get(
        WORKSPACES, {"organization": org.source_id, "connected": "no"}
    ).json()

    assert row["connected_count"] == 1
    assert row["managers_count"] == 2
    assert row["origin"] is None
    assert connected["count"] == 1
    assert disconnected["count"] == 0


@reporting
def test_queue_follows_the_latest_task():
    """A pending space is running only once its latest task has started."""
    client, user = _client()
    org = factories.OrganizationFactory()
    org.admins.add(user)
    running = factories.WorkspaceFactory(organization=org, title="Running")
    running.set_destination_status("drive", Workspace.Status.PENDING)
    running.save()
    _run(running, "run", status="STARTED")
    queued = factories.WorkspaceFactory(organization=org, title="Queued")
    queued.set_destination_status("drive", Workspace.Status.PENDING)
    queued.save()
    _run(queued, "wait", status="PENDING")

    rows = {
        row["title"]: row
        for row in client.get(
            WORKSPACES, {"organization": org.source_id, "status": "PENDING"}
        ).json()["results"]
    }

    assert rows["Running"]["queue"] == "running"
    assert rows["Queued"]["queue"] == "queued"


@reporting
def test_origin_admin_matches_nothing():
    """Administrator launches are not recorded yet."""
    client, user = _client()
    org = factories.OrganizationFactory()
    org.admins.add(user)
    workspace = factories.WorkspaceFactory(organization=org)
    _run(workspace, "by-manager")

    admin = client.get(
        WORKSPACES, {"organization": org.source_id, "origin": "admin"}
    ).json()
    manager = client.get(
        WORKSPACES, {"organization": org.source_id, "origin": "manager"}
    ).json()

    assert admin["count"] == 0
    assert manager["count"] == 1


@reporting
def test_locked_filter_is_empty():
    """Resana locks are not stored, so the locked filter stays empty."""
    client, user = _client()
    org = factories.OrganizationFactory()
    org.admins.add(user)
    factories.WorkspaceFactory(organization=org)

    body = client.get(
        WORKSPACES, {"organization": org.source_id, "status": "LOCKED"}
    ).json()

    assert body["count"] == 0
    assert body["eligible_count"] == 0


@reporting
def test_target_uses_the_destination_that_matches_the_status():
    """When both destinations are set, the one carrying the aggregate status wins."""
    client, user = _client()
    org = factories.OrganizationFactory()
    org.admins.add(user)
    workspace = factories.WorkspaceFactory(organization=org, title="Deux cibles")
    workspace.set_destination_status("drive", Workspace.Status.SUCCESS)
    workspace.set_destination_status("archive", Workspace.Status.FAILURE)
    workspace.save()

    row = client.get(WORKSPACES, {"organization": org.source_id}).json()["results"][0]

    assert workspace.status == Workspace.Status.FAILURE
    assert row["target"] == "archive"
    assert row["status"] == "FAILURE"


@reporting
def test_workspaces_ordered_by_title_by_default():
    """The default order is the title, ascending."""
    client, user = _client()
    org = factories.OrganizationFactory()
    org.admins.add(user)
    factories.WorkspaceFactory(organization=org, title="Bravo")
    factories.WorkspaceFactory(organization=org, title="Alpha")

    titles = [
        row["title"]
        for row in client.get(WORKSPACES, {"organization": org.source_id}).json()[
            "results"
        ]
    ]

    assert titles == ["Alpha", "Bravo"]


def _managers_path(workspace_id):
    return f"/api/v1.0/admin/workspaces/{workspace_id}/managers"


@reporting
def test_managers_lists_members_and_marks_connected_ones():
    """Connected managers come first and include last_login."""
    client, user = _client()
    org = factories.OrganizationFactory()
    org.admins.add(user)
    workspace = factories.WorkspaceFactory(
        organization=org,
        members=[
            {
                "name": "Roux",
                "firstName": "Paul",
                "email": "paul.roux@dgad.gouv.fr",
            },
            {
                "name": "Martin",
                "firstName": "Claire",
                "email": "claire.martin@dgad.gouv.fr",
            },
        ],
    )
    login_time = timezone.now()
    manager = factories.UserFactory(
        email="claire.martin@dgad.gouv.fr", last_login=login_time
    )
    manager.workspaces.add(workspace)

    response = client.get(_managers_path(workspace.id))

    assert response.status_code == 200
    body = response.json()
    assert body["connected_count"] == 1
    assert body["managers_count"] == 2
    assert body["results"][0]["connected"] is True
    assert body["results"][0]["email"] == "claire.martin@dgad.gouv.fr"
    assert body["results"][0]["last_login"] is not None
    assert body["results"][1]["connected"] is False
    assert body["results"][1]["last_login"] is None


@reporting
def test_managers_rejects_a_foreign_workspace():
    client, user = _client()
    org = factories.OrganizationFactory()
    org.admins.add(user)
    foreign = factories.OrganizationFactory()
    workspace = factories.WorkspaceFactory(organization=foreign)

    response = client.get(_managers_path(workspace.id))

    assert response.status_code == 403
    assert response.json()["error_name"] == "NotOrganizationAdmin"


def test_managers_anonymous():
    workspace = factories.WorkspaceFactory()
    response = APIClient().get(_managers_path(workspace.id))
    assert response.status_code == 401


STATS = "/api/v1.0/admin/stats"


@reporting
def test_stats_count_the_whole_organization():
    """Tiles count every workspace, and both destinations of a double success."""
    client, user = _client()
    organization = factories.OrganizationFactory(
        source_id="org", last_sync=timezone.now()
    )
    organization.admins.add(user)
    factories.WorkspaceFactory(organization=organization, title="Fresh")
    failed = factories.WorkspaceFactory(organization=organization, title="Failed")
    failed.set_destination_status("drive", Workspace.Status.FAILURE)
    failed.save()
    both = factories.WorkspaceFactory(organization=organization, title="Both")
    both.set_destination_status("drive", Workspace.Status.SUCCESS)
    both.set_destination_status("archive", Workspace.Status.SUCCESS)
    both.save()
    pending = factories.WorkspaceFactory(organization=organization, title="Running")
    pending.set_destination_status("drive", Workspace.Status.PENDING)
    pending.save()
    factories.WorkspaceFactory(title="Elsewhere")

    response = client.get(STATS, {"organization": "org"})

    assert response.status_code == 200
    body = response.json()
    assert body["last_sync"] is not None
    assert body["resana"] == 4
    assert body["not_migrated"] == 2
    assert body["failure"] == 1
    assert body["pending"] == 1
    assert body["drive"] == 1
    assert body["archive"] == 1
    assert body["locked"] == 0
    assert body["connected"] == 0


def test_stats_use_one_query(django_assert_num_queries):
    """The tile counts share a single aggregate."""
    organization = factories.OrganizationFactory()
    factories.WorkspaceFactory(organization=organization)
    with django_assert_num_queries(1):
        stats = organization_stats(organization)
    assert stats["resana"] == 1
    assert stats["locked"] == 0


def test_stats_anonymous():
    response = APIClient().get(STATS, {"organization": "org"})
    assert response.status_code == 401
