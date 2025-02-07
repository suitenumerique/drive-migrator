"""Admin classes and registrations for core app."""
from django.contrib import admin, messages
from django.contrib.auth import admin as auth_admin
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from core.api.views.workspaces_process import push_workspace_task

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

    def get_readonly_fields(self, request, obj=None):
        fields = ("id", "sub", "created_at", "updated_at")
        return fields


class ExtraTaskInfoAdminInline(admin.TabularInline):
    model = models.ExtraTaskInfo
    can_delete = False
    readonly_fields = [
        "task_result",
        "get_task",
        "get_task_status",
        "get_task_date_created",
        "get_task_date_done",
    ]
    max_num = 0

    def get_task(self, obj):
        return mark_safe(  # noqa: S308
            '<a href="%s">%s</a>'
            % (
                reverse(
                    "admin:django_celery_results_taskresult_change",
                    args=(obj.task_result.id,),
                ),
                obj.task_result.id,
            )
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
        "osmose_id",
        "title",
        "status",
        "status_archive",
        "status_resana",
        "resana_files_success",
        "resana_files_error",
    )
    list_filter = ("status", "status_archive", "status_resana")
    search_fields = ("id", "osmose_id", "title")
    inlines = [ExtraTaskInfoAdminInline]
    change_form_template = "admin/workspace_retry_failed.html"

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

            if obj.status_archive == Workspace.Status.FAILURE:
                obj.set_status_archive(Workspace.Status.PENDING)

            if obj.status_resana == Workspace.Status.FAILURE:
                obj.set_status_resana(Workspace.Status.PENDING)
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
    ]

    def get_workspace(self, obj):
        return mark_safe(  # noqa: S308
            '<a href="%s">%s</a>'
            % (
                reverse("admin:core_workspace_change", args=(obj.workspace.id,)),
                obj.workspace.title,
            )
        )

    get_workspace.short_description = "Workspace"

    def get_user(self, obj):
        return mark_safe(  # noqa: S308
            '<a href="%s">%s</a>'
            % (
                reverse("admin:core_user_change", args=(obj.user.id,)),
                obj.user.email,
            )
            if obj.user
            else "None"
        )

    get_user.short_description = "User"

    def get_task(self, obj):
        return mark_safe(  # noqa: S308
            '<a href="%s">%s</a>'
            % (
                reverse(
                    "admin:django_celery_results_taskresult_change",
                    args=(obj.task_result.id,),
                ),
                obj.task_result.id,
            )
        )

    get_task.short_description = "Task"

    def get_task_status(self, obj):
        return obj.task_result.status

    def get_task_date_created(self, obj):
        return obj.task_result.date_created

    def get_task_date_done(self, obj):
        return obj.task_result.date_done
