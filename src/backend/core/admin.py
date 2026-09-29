"""Admin classes and registrations for core app."""
import csv

from django import forms
from django.contrib import admin, messages
from django.contrib.auth import admin as auth_admin
from django.core.exceptions import PermissionDenied
from django.db.models import F
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _

from core.api.views.workspaces_process import push_workspace_task
from core.destinations.drive.drive_backend import clear_drive_tokens
from core.destinations.resana.resana_backend import ResanaBackend
from core.processing.integrity import REPORT_COLUMNS, Stage, rows_by_severity
from core.sources.resana.token_manager import ResanaTokenManager

from . import models
from .models import ExtraTaskInfo, Workspace


@admin.register(models.User)
class UserAdmin(auth_admin.UserAdmin):
    """Admin class for the User model"""

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "id",
                    "admin_email",
                    "password",
                )
            },
        ),
        (_("Personal info"), {"fields": ("sub", "email", "language", "timezone")}),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_device",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        (_("Important dates"), {"fields": ("created_at", "updated_at")}),
        (
            _("OIDC tokens"),
            {
                "fields": (
                    "oidc_access_token",
                    "oidc_refresh_token",
                    "oidc_token_expires_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2"),
            },
        ),
    )
    list_display = (
        "id",
        "sub",
        "admin_email",
        "email",
        "is_active",
        "is_staff",
        "is_superuser",
        "is_device",
        "created_at",
        "updated_at",
    )
    list_filter = ("is_staff", "is_superuser", "is_device", "is_active")
    ordering = ("is_active", "-is_superuser", "-is_staff", "-is_device", "-updated_at")
    search_fields = ("id", "sub", "admin_email", "email")
    actions = ["reset_resana_connection", "reset_drive_connection"]

    def reset_resana_connection(self, request, queryset):
        for user in queryset:
            ResanaTokenManager(user).clear_tokens()
        self.message_user(
            request, f"Resana connection reset for {queryset.count()} user(s)."
        )

    reset_resana_connection.short_description = "Reset Resana connection"

    def reset_drive_connection(self, request, queryset):
        for user in queryset:
            clear_drive_tokens(user)
        self.message_user(
            request, f"Drive connection reset for {queryset.count()} user(s)."
        )

    reset_drive_connection.short_description = "Reset Drive connection"

    def get_readonly_fields(self, request, obj=None):
        fields = (
            "id",
            "sub",
            "created_at",
            "updated_at",
            "oidc_access_token",
            "oidc_refresh_token",
            "oidc_token_expires_at",
        )
        return fields


def format_integrity_stages(stages: dict | None) -> str:
    """Render per-stage counts as "stage: count" pairs, sorted by stage."""
    if not stages:
        return "-"
    return ", ".join(f"{stage}: {count}" for stage, count in sorted(stages.items()))


# The detail page only shows the most severe rows; the CSV has them all.
INTEGRITY_FILES_DISPLAY_LIMIT = 50


def with_integrity_counts(queryset):
    """Read the report's counters from the database instead of loading the
    whole per-file listing of every run."""
    return queryset.defer("integrity_report").annotate(
        integrity_stages=F("integrity_report__summary__stages"),
        integrity_migrated=F("integrity_report__summary__migrated_files_count"),
        integrity_source=F("integrity_report__summary__source_files_count"),
    )


def format_files_migrated(obj) -> str:
    """ "migrated/source" file counts of a run annotated by with_integrity_counts."""
    if obj.integrity_migrated is None:
        return "-"
    files_migrated = f"{obj.integrity_migrated}/{obj.integrity_source}"
    # Neither migrated nor lost: Drive has not finished analyzing them yet.
    analyzing = (obj.integrity_stages or {}).get(Stage.ANALYSIS_UNFINISHED)
    if analyzing:
        files_migrated += f" ({analyzing} analyzing)"
    return files_migrated


class IntegrityTrackingSelect(forms.NullBooleanSelect):
    """NullBooleanSelect whose empty choice explains what None means."""

    def __init__(self, attrs=None):
        super().__init__(attrs)
        self.choices = (
            ("unknown", _("Follow the feature flag")),
            ("true", _("Yes")),
            ("false", _("No")),
        )


class ExtraTaskInfoAdminInline(admin.TabularInline):
    model = models.ExtraTaskInfo
    can_delete = False
    exclude = ["integrity_report"]
    readonly_fields = [
        "task_result",
        "get_task",
        "get_task_status",
        "get_task_date_created",
        "get_task_date_done",
        "integrity_check_passed",
        "get_files_migrated",
        "get_integrity_stages",
    ]
    max_num = 0

    def get_queryset(self, request):
        return with_integrity_counts(super().get_queryset(request))

    def get_files_migrated(self, obj):
        return format_files_migrated(obj)

    get_files_migrated.short_description = "Files migrated"

    def get_integrity_stages(self, obj):
        return format_html(
            '<a href="{}">{}</a>',
            reverse("admin:core_extrataskinfo_change", args=(obj.id,)),
            format_integrity_stages(obj.integrity_stages),
        )

    get_integrity_stages.short_description = "Integrity"

    def get_task(self, obj):
        return format_html(
            '<a href="{}">{}</a>',
            reverse(
                "admin:django_celery_results_taskresult_change",
                args=(obj.task_result.id,),
            ),
            obj.task_result.id,
        )

    get_task.short_description = "Task"

    def get_task_status(self, obj):
        return obj.task_result.status

    def get_task_date_created(self, obj):
        return obj.task_result.date_created

    def get_task_date_done(self, obj):
        return obj.task_result.date_done


@admin.register(models.Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "source_id",
        "source_type",
        "title",
        "status",
        "destination_statuses",
        "get_migration_user_email",
    )
    list_filter = ("status", "source_type", "is_file_integrity_tracked")
    search_fields = (
        "id",
        "source_id",
        "title",
        "migration_user__email",
    )
    inlines = [ExtraTaskInfoAdminInline]

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "is_file_integrity_tracked":
            kwargs["widget"] = IntegrityTrackingSelect
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    change_form_template = "admin/workspace_retry_failed.html"

    actions = ["export_as_csv"]

    def get_migration_user_email(self, obj):
        return obj.migration_user.email if obj.migration_user else ""

    get_migration_user_email.short_description = "User email"
    get_migration_user_email.admin_order_field = "migration_user__email"

    def export_as_csv(self, request, queryset):
        meta = self.model._meta  # noqa: SLF001

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f"attachment; filename={meta}.csv"
        writer = csv.writer(response)

        backend = ResanaBackend()

        writer.writerow(
            ["user", "domain", "titre", "destination", "date", "archive", "resana"]
        )
        for workspace in queryset:
            writer.writerow(self._export_csv_row(backend, workspace))

        return response

    @staticmethod
    def _export_csv_row(backend, workspace):
        """domain email, titre du workspace, organisation destination, date,
        archive (o/n), resana (o/n)"""
        user = workspace.migration_user
        task_info = (
            ExtraTaskInfo.objects.filter(workspace=workspace).order_by("-id").first()
        )
        return [
            user.email if user else "",
            user.email.split("@")[1] if user else "",
            workspace.title,
            (
                backend.get_mapping_from_email(user.email).resana_organization_name
                if user
                else ""
            ),
            task_info.task_result.date_done if task_info else "",
            workspace.get_destination_status("archive"),
            workspace.get_destination_status("resana"),
        ]

    export_as_csv.short_description = "Export Selected"

    def change_view(self, request, object_id, form_url="", extra_context=None):
        obj = Workspace.objects.get(id=object_id)

        return super().change_view(
            request,
            object_id,
            form_url,
            extra_context={"obj": obj},
        )

    def response_change(self, request, obj):
        if "_retry-failed" in request.POST:
            if obj.status != Workspace.Status.FAILURE:
                messages.error(request, "Can only retry failed workspaces.")
                return HttpResponseRedirect(".")

            for dest_name, status in obj.destination_statuses.items():
                if status == Workspace.Status.FAILURE:
                    obj.set_destination_status(dest_name, Workspace.Status.PENDING)
            obj.save()

            push_workspace_task(obj, obj.migration_user)
            return HttpResponseRedirect(".")

        return super().response_change(request, obj)


@admin.register(models.FeatureFlag)
class FeatureFlagAdmin(admin.ModelAdmin):
    pass


@admin.register(models.ResanaEmailMapping)
class ResanaEmailMappingAdmin(admin.ModelAdmin):
    search_fields = (
        "id",
        "domain",
        "resana_organization_name",
        "resana_organization_uuid",
    )


@admin.register(models.ExtraTaskInfo)
class ExtraTaskInfoAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "get_workspace",
        "get_user",
        "get_task",
        "get_task_status",
        "get_task_date_created",
        "get_task_date_done",
        "integrity_check_passed",
        "get_files_migrated",
    ]
    list_filter = ["integrity_check_passed"]
    exclude = ["integrity_report"]
    readonly_fields = [
        "integrity_check_passed",
        "get_integrity_summary",
        "get_integrity_files",
    ]
    actions = ["export_integrity_csv"]

    def get_queryset(self, request):
        # The per-file listing can weigh megabytes: loaded only when displayed.
        return with_integrity_counts(super().get_queryset(request))

    def get_files_migrated(self, obj):
        return format_files_migrated(obj)

    get_files_migrated.short_description = "Files migrated"

    def get_integrity_summary(self, obj):
        summary = obj.integrity_report.get("summary")
        if not summary:
            return "No integrity report for this run."
        return format_html_join(
            "",
            "<div>{}: {}</div>",
            (
                (key, format_integrity_stages(value) if key == "stages" else value)
                for key, value in summary.items()
            ),
        )

    get_integrity_summary.short_description = "Integrity summary"

    def get_integrity_files(self, obj):
        rows = obj.integrity_report.get("files")
        if not rows:
            return "No integrity report for this run."
        shown_rows = rows_by_severity(rows)[:INTEGRITY_FILES_DISPLAY_LIMIT]
        csv_link = format_html(
            '<p><a href="{}">Download the full listing as CSV</a></p>',
            reverse("admin:core_extrataskinfo_integrity_csv", args=(obj.id,)),
        )
        if len(shown_rows) < len(rows):
            csv_link = format_html(
                "<p>Showing {} of {} rows, most severe first.</p>{}",
                len(shown_rows),
                len(rows),
                csv_link,
            )
        header = format_html_join(
            "", "<th>{}</th>", ((column,) for column in REPORT_COLUMNS)
        )
        body = format_html_join(
            "",
            "<tr>{}</tr>",
            (
                (
                    format_html_join(
                        "",
                        "<td>{}</td>",
                        ((row.get(column, ""),) for column in REPORT_COLUMNS),
                    ),
                )
                for row in shown_rows
            ),
        )
        return format_html(
            "{}<table><thead><tr>{}</tr></thead><tbody>{}</tbody></table>",
            csv_link,
            header,
            body,
        )

    get_integrity_files.short_description = "Integrity files"

    def export_integrity_csv(self, request, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = "attachment; filename=integrity_report.csv"
        writer = csv.DictWriter(
            response,
            fieldnames=["task_id", "workspace_id", *REPORT_COLUMNS],
            extrasaction="ignore",
        )
        writer.writeheader()
        # get_queryset() defers the report: load it with the runs, not per run.
        for extra_task in queryset.defer(None).select_related("task_result"):
            for row in extra_task.integrity_report.get("files", []):
                writer.writerow(
                    {
                        "task_id": extra_task.task_result.task_id,
                        "workspace_id": extra_task.workspace_id,
                        **row,
                    }
                )
        return response

    export_integrity_csv.short_description = "Export integrity report as CSV"

    def get_urls(self):
        return [
            path(
                "<path:object_id>/integrity-csv/",
                self.admin_site.admin_view(self.integrity_csv_view),
                name="core_extrataskinfo_integrity_csv",
            ),
            *super().get_urls(),
        ]

    def integrity_csv_view(self, request, object_id):
        """CSV export of a single run, linked from its detail page."""
        if not self.has_view_permission(request):
            raise PermissionDenied
        # get_object() returns None for an unknown or malformed id.
        extra_task = self.get_object(request, object_id)
        if extra_task is None:
            raise Http404
        return self.export_integrity_csv(
            request, ExtraTaskInfo.objects.filter(pk=extra_task.pk)
        )

    def get_workspace(self, obj):
        return format_html(
            '<a href="{}">{}</a>',
            reverse("admin:core_workspace_change", args=(obj.workspace.id,)),
            obj.workspace.title,
        )

    get_workspace.short_description = "Workspace"

    def get_user(self, obj):
        if not obj.user:
            return "None"
        return format_html(
            '<a href="{}">{}</a>',
            reverse("admin:core_user_change", args=(obj.user.id,)),
            obj.user.email,
        )

    get_user.short_description = "User"

    def get_task(self, obj):
        return format_html(
            '<a href="{}">{}</a>',
            reverse(
                "admin:django_celery_results_taskresult_change",
                args=(obj.task_result.id,),
            ),
            obj.task_result.id,
        )

    get_task.short_description = "Task"

    def get_task_status(self, obj):
        return obj.task_result.status

    def get_task_date_created(self, obj):
        return obj.task_result.date_created

    def get_task_date_done(self, obj):
        return obj.task_result.date_done
