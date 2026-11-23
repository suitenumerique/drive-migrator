"""Read models for the organization admin reporting API.

Organizations and their workspaces are stored locally. These helpers only
read that data: they do not call Resana.
"""

from django.db import connection
from django.db.models import (
    BooleanField,
    Case,
    CharField,
    Count,
    DateTimeField,
    Exists,
    F,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    TextField,
    Value,
    When,
)
from django.db.models.expressions import RawSQL
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Coalesce

from core.api import APIException
from core.api.views.workspaces import with_latest_integrity
from core.models import ExtraTaskInfo, FeatureFlag, Organization, User, Workspace

ORDERING_FIELDS = {
    "title",
    "status",
    "connected_count",
    "source_files_count",
    "migrated_at",
}
STATUS_FILTERS = {
    "not_migrated",
    "NONE",
    "PENDING",
    "migrated",
    "GAP",
    "FAILURE",
    "LOCKED",
}
PAGE_SIZES = {"25", "50", "100"}
RUNNING_TASK_STATUS = "STARTED"


def admin_reporting_is_enabled() -> bool:
    """True only when the flag row exists and is active.

    Unlike core.utils.is_feature(), a missing row stays disabled.
    """
    return FeatureFlag.objects.filter(
        name=FeatureFlag.Name.ADMIN_REPORTING, is_active=True
    ).exists()


def ensure_workspace_admin(user, workspace: Workspace) -> Organization:
    """Refuse when the workspace has no organization or the user is not its admin."""
    organization = workspace.organization
    if organization is None or not organization.admins.filter(pk=user.pk).exists():
        raise APIException("NotOrganizationAdmin")
    return organization


def manager_rows(workspace: Workspace) -> list[dict]:
    """Animateurs of one space, connected ones first.

    Each row has name, email, connected and last_login. last_login is set
    only when the animator has an account linked to this workspace.
    """
    raw_members = workspace.members if isinstance(workspace.members, list) else []
    linked = {
        account.email.lower(): account.last_login
        for account in User.objects.filter(workspaces=workspace).exclude(email="")
        if account.email
    }
    rows = []
    for entry in raw_members:
        if not isinstance(entry, dict):
            continue
        email = (entry.get("email") or "").strip()
        first = (entry.get("firstName") or "").strip()
        last = (entry.get("name") or "").strip()
        label = " ".join(part for part in (first, last) if part).strip()
        if not label:
            label = email or "—"
        connected = bool(email and email.lower() in linked)
        rows.append(
            {
                "name": label,
                "email": email,
                "connected": connected,
                "last_login": linked.get(email.lower()) if connected else None,
            }
        )
    rows.sort(
        key=lambda row: (
            not row["connected"],
            row["name"].casefold(),
            row["email"].casefold(),
        )
    )
    return rows


def require_organization(user, source_id: str | None) -> Organization:
    """Return the organization `user` administers, or refuse the request.

    An unknown id and an organization the user does not administer both
    answer NotOrganizationAdmin.
    """
    if not source_id:
        raise APIException("InvalidQueryParam")
    organization = Organization.objects.filter(source_id=source_id, admins=user).first()
    if organization is None:
        raise APIException("NotOrganizationAdmin")
    return organization


def parse_list_params(params) -> dict:
    """Validate the list query string. Raises InvalidQueryParam."""
    page_size = params.get("page_size")
    if page_size in (None, ""):
        page_size = "50"
    elif page_size not in PAGE_SIZES:
        raise APIException("InvalidQueryParam")

    ordering = params.get("ordering")
    if ordering in (None, ""):
        ordering = "title"
    field = ordering[1:] if ordering.startswith("-") else ordering
    if field not in ORDERING_FIELDS:
        raise APIException("InvalidQueryParam")

    return {
        "page_size": int(page_size),
        "ordering": ordering,
        "search": (params.get("search") or "").strip(),
        "status": _choice(params, "status", STATUS_FILTERS),
        "target": _choice(params, "target", {"drive", "archive"}),
        "connected": _choice(params, "connected", {"yes", "no"}),
        "origin": _choice(params, "origin", {"manager", "admin"}),
    }


def admin_workspaces(organization: Organization, params: dict):
    """Return the filtered, annotated, ordered workspaces of one organization."""
    queryset = annotate_admin_workspaces(
        Workspace.objects.filter(organization=organization)
    )
    queryset = _apply_filters(queryset, params)
    return queryset.order_by(params["ordering"], "id")


def organization_stats(organization: Organization) -> dict:
    """Counts for the whole organization, not the current page.

    One aggregate. Status counts read Workspace.status. Drive and archive
    each count a successful destination, so a workspace migrated to both is
    counted twice. Only the connected tile needs the member-link subquery.
    Locked workspaces are not stored, so that count stays zero.
    """
    totals = (
        Workspace.objects.filter(organization=organization)
        .annotate(connected_count=_connected_count_sql())
        .aggregate(
            resana=Count("pk"),
            connected=Count("pk", filter=Q(connected_count__gt=0)),
            not_migrated=Count(
                "pk",
                filter=Q(status__in=[Workspace.Status.NONE, Workspace.Status.FAILURE]),
            ),
            pending=Count("pk", filter=Q(status=Workspace.Status.PENDING)),
            failure=Count("pk", filter=Q(status=Workspace.Status.FAILURE)),
            drive=Count(
                "pk",
                filter=Q(destination_statuses__drive=Workspace.Status.SUCCESS),
            ),
            archive=Count(
                "pk",
                filter=Q(destination_statuses__archive=Workspace.Status.SUCCESS),
            ),
        )
    )
    return {
        "last_sync": organization.last_sync,
        "resana": totals["resana"],
        "connected": totals["connected"],
        "not_migrated": totals["not_migrated"],
        "locked": 0,
        "drive": totals["drive"],
        "archive": totals["archive"],
        "pending": totals["pending"],
        "failure": totals["failure"],
    }


def eligible_count(queryset, status_filter: str | None) -> int:
    """How many rows of `queryset` an administrator could launch.

    NONE and FAILURE, plus LOCKED only when the list is filtered on LOCKED.
    Locked workspaces are not stored yet, so that branch stays empty.
    """
    if status_filter == "LOCKED":
        return queryset.filter(status="LOCKED").count()
    return queryset.filter(
        status__in=[Workspace.Status.NONE, Workspace.Status.FAILURE]
    ).count()


def display_status(workspace: Workspace) -> str:
    """SUCCESS with refused files or a failed integrity check is shown as GAP."""
    if _is_gap(workspace):
        return "GAP"
    return workspace.status


def annotate_admin_workspaces(queryset):
    """Add the derived columns the list filters, sorts and serializes."""
    none = Value(Workspace.Status.NONE, output_field=TextField())
    drive_status = Cast(
        Coalesce(
            KeyTextTransform("drive", "destination_statuses"),
            none,
            output_field=TextField(),
        ),
        output_field=CharField(),
    )
    archive_status = Cast(
        Coalesce(
            KeyTextTransform("archive", "destination_statuses"),
            none,
            output_field=TextField(),
        ),
        output_field=CharField(),
    )
    queryset = with_latest_integrity(queryset).annotate(
        drive_status=drive_status,
        archive_status=archive_status,
        has_run=Exists(ExtraTaskInfo.objects.filter(workspace=OuterRef("pk"))),
        latest_task_status=Subquery(
            ExtraTaskInfo.objects.filter(workspace=OuterRef("pk"))
            .order_by("-id")
            .values("task_result__status")[:1]
        ),
        migrated_at=Subquery(
            ExtraTaskInfo.objects.filter(
                workspace=OuterRef("pk"),
                task_result__status="SUCCESS",
            )
            .order_by("-id")
            .values("task_result__date_done")[:1],
            output_field=DateTimeField(),
        ),
        connected_count=_connected_count_sql(),
        managers_count=_managers_count_sql(),
    )
    return queryset.annotate(
        admin_target=Case(
            When(
                ~Q(drive_status=Workspace.Status.NONE)
                & Q(archive_status=Workspace.Status.NONE),
                then=Value("drive"),
            ),
            When(
                Q(drive_status=Workspace.Status.NONE)
                & ~Q(archive_status=Workspace.Status.NONE),
                then=Value("archive"),
            ),
            When(
                ~Q(drive_status=Workspace.Status.NONE)
                & ~Q(archive_status=Workspace.Status.NONE)
                & Q(drive_status=F("status")),
                then=Value("drive"),
            ),
            When(
                ~Q(drive_status=Workspace.Status.NONE)
                & ~Q(archive_status=Workspace.Status.NONE)
                & Q(archive_status=F("status")),
                then=Value("archive"),
            ),
            When(
                ~Q(drive_status=Workspace.Status.NONE)
                & ~Q(archive_status=Workspace.Status.NONE),
                then=Value("drive"),
            ),
            default=Value(None),
            output_field=CharField(null=True),
        ),
        source_files_count=_source_files_count(),
    )


def _apply_filters(queryset, params: dict):
    status = params["status"]
    if status == "LOCKED":
        queryset = queryset.none()
    elif status == "not_migrated":
        queryset = queryset.filter(
            status__in=[Workspace.Status.NONE, Workspace.Status.FAILURE]
        )
    elif status == "migrated":
        queryset = queryset.filter(status=Workspace.Status.SUCCESS)
    elif status == "GAP":
        queryset = queryset.filter(_gap_q())
    elif status is not None:
        queryset = queryset.filter(status=status)

    if params["target"]:
        queryset = queryset.exclude(status=Workspace.Status.NONE).filter(
            admin_target=params["target"]
        )
    if params["connected"] == "yes":
        queryset = queryset.filter(connected_count__gt=0)
    elif params["connected"] == "no":
        queryset = queryset.filter(connected_count=0)
    if params["origin"] == "manager":
        queryset = queryset.filter(has_run=True)
    elif params["origin"] == "admin":
        # No route records an administrator launch yet.
        queryset = queryset.none()
    if params["search"]:
        queryset = _apply_search(queryset, params["search"])
    return queryset


def _apply_search(queryset, term: str):
    match = RawSQL(
        _members_match_sql(),
        [term],
        output_field=BooleanField(),
    )
    return queryset.annotate(member_match=match).filter(
        Q(title__unaccent__icontains=term) | Q(member_match=True)
    )


def _choice(params, name: str, allowed: set[str]) -> str | None:
    value = params.get(name)
    if value in (None, ""):
        return None
    if value not in allowed:
        raise APIException("InvalidQueryParam")
    return value


def _gap_q() -> Q:
    return Q(status=Workspace.Status.SUCCESS) & (
        Q(integrity_check_passed=False) | ~Q(upload_errors=[])
    )


def _is_gap(workspace: Workspace) -> bool:
    if workspace.status != Workspace.Status.SUCCESS:
        return False
    if workspace.upload_errors:
        return True
    return workspace.integrity_check_passed is False


def workspace_queue(workspace: Workspace) -> str | None:
    if workspace.status != Workspace.Status.PENDING:
        return None
    if workspace.latest_task_status == RUNNING_TASK_STATUS:
        return "running"
    return "queued"


def _source_files_count() -> RawSQL:
    """Integer file count of the latest run, so the column can be ordered.

    The shared integrity annotation is jsonb and does not cast to integer.
    """
    report = _q(ExtraTaskInfo._meta.db_table)  # noqa: SLF001
    workspace = _q(Workspace._meta.db_table)  # noqa: SLF001
    sql = (
        "(SELECT CAST("  # noqa: S608
        + report
        + ".integrity_report #>> '{summary,source_files_count}' AS integer) FROM "
        + report
        + " WHERE "
        + report
        + ".workspace_id = "
        + workspace
        + ".id ORDER BY "
        + report
        + ".id DESC LIMIT 1)"
    )
    return RawSQL(sql, [], output_field=IntegerField())


def _json_members_expr() -> str:
    members = f"{_q(Workspace._meta.db_table)}.members"  # noqa: SLF001
    return (
        "CASE WHEN jsonb_typeof("
        + members
        + ") = 'array' THEN "
        + members
        + " ELSE '[]'::jsonb END"
    )


def _connected_count_sql() -> RawSQL:
    link = User.workspaces.through
    members = _json_members_expr()
    sql = (
        "(SELECT COUNT(DISTINCT lower(member->>'email')) FROM jsonb_array_elements("  # noqa: S608
        + members
        + ") AS member WHERE COALESCE(member->>'email', '') <> '' AND EXISTS ("
        + "SELECT 1 FROM "
        + _q(User._meta.db_table)  # noqa: SLF001
        + " AS account INNER JOIN "
        + _q(link._meta.db_table)  # noqa: SLF001
        + " AS link ON link."
        + _q(link._meta.get_field("user").column)  # noqa: SLF001
        + " = account."
        + _q(User._meta.pk.column)  # noqa: SLF001
        + " WHERE link."
        + _q(link._meta.get_field("workspace").column)  # noqa: SLF001
        + " = "
        + _q(Workspace._meta.db_table)  # noqa: SLF001
        + "."
        + _q(Workspace._meta.pk.column)  # noqa: SLF001
        + " AND lower(account."
        + _q(User._meta.get_field("email").column)  # noqa: SLF001
        + ") = lower(member->>'email')))"
    )
    return RawSQL(sql, [], output_field=IntegerField())


def _managers_count_sql() -> RawSQL:
    sql = "(SELECT jsonb_array_length(" + _json_members_expr() + "))"
    return RawSQL(sql, [], output_field=IntegerField())


def _members_match_sql() -> str:
    return (
        "EXISTS (SELECT 1 FROM jsonb_array_elements("  # noqa: S608
        + _json_members_expr()
        + ") AS member WHERE strpos(lower(unaccent(concat_ws(' ', "
        + "member->>'firstName', member->>'name', member->>'email'))), "
        + "lower(unaccent(%s))) > 0)"
    )


def _q(name: str) -> str:
    return connection.ops.quote_name(name)
