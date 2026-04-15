"""
Declare and configure the models for the core application
"""
import uuid
from logging import getLogger

from django.conf import settings
from django.contrib.auth import models as auth_models
from django.contrib.auth.base_user import AbstractBaseUser
from django.core import mail, validators
from django.db import models
from django.utils.functional import lazy
from django.utils.translation import gettext_lazy as _

from django_celery_results.models import TaskResult
from timezone_field import TimeZoneField

logger = getLogger(__name__)


class BaseModel(models.Model):
    """
    Serves as an abstract base model for other models, ensuring that records are validated
    before saving as Django doesn't do it by default.

    Includes fields common to all models: a UUID primary key and creation/update timestamps.
    """

    id = models.UUIDField(
        verbose_name=_("id"),
        help_text=_("primary key for the record as UUID"),
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    created_at = models.DateTimeField(
        verbose_name=_("created on"),
        help_text=_("date and time at which a record was created"),
        auto_now_add=True,
        editable=False,
    )
    updated_at = models.DateTimeField(
        verbose_name=_("updated on"),
        help_text=_("date and time at which a record was last updated"),
        auto_now=True,
        editable=False,
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        """Call `full_clean` before saving."""
        self.full_clean()
        super().save(*args, **kwargs)


class Workspace(BaseModel):
    class Status(models.TextChoices):
        NONE = "NONE"
        PENDING = "PENDING"
        FAILURE = "FAILURE"
        SUCCESS = "SUCCESS"

    source_id = models.CharField(
        help_text=_("id of the workspace on the source platform"),
        unique=True,
        default="",
    )
    source_type = models.CharField(
        help_text=_("source backend type, e.g. 'osmose', 'filesystem'"),
        default="",
    )

    migration_user = models.ForeignKey("User", models.SET_NULL, blank=True, null=True)

    title = models.CharField()

    # Do not edit this field directly. Use set_destination_status instead.
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.NONE,
    )

    # Keys are destination names (e.g. "archive", "resana", "drive").
    # Do not edit this field directly. Use set_destination_status instead.
    destination_statuses = models.JSONField(default=dict, blank=True)

    # Arbitrary per-destination metadata (IDs, job IDs, file counts, etc.).
    destination_metadata = models.JSONField(default=dict, blank=True)

    # Workspace members populated by the source backend during prepare_export().
    # Each entry: {"name": str, "firstName": str, "email": str}
    members = models.JSONField(default=list, blank=True)

    def get_destination_status(self, destination_name: str) -> str:
        return self.destination_statuses.get(destination_name, Workspace.Status.NONE)

    def set_destination_status(self, destination_name: str, status: str) -> None:
        self.destination_statuses[destination_name] = status
        self.sync_status()

    def get_destination_metadata(self, destination_name: str) -> dict:
        return self.destination_metadata.get(destination_name, {})

    def set_destination_metadata(self, destination_name: str, data: dict) -> None:
        self.destination_metadata[destination_name] = data

    def sync_status(self):
        self.status = self.compute_status()

    def compute_status(self) -> str:
        """Generic version — works with any number of destinations."""
        statuses = list(self.destination_statuses.values())
        if not statuses or all(s == self.Status.NONE for s in statuses):
            return self.Status.NONE
        if any(s == self.Status.PENDING for s in statuses):
            return self.Status.PENDING
        if any(s == self.Status.FAILURE for s in statuses):
            return self.Status.FAILURE
        return self.Status.SUCCESS


class ExtraTaskInfo(models.Model):
    task_result = models.OneToOneField(
        TaskResult,
        on_delete=models.CASCADE,
    )

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)

    # Should not be null, but we need to allow it for the initial migration.
    user = models.ForeignKey("User", on_delete=models.CASCADE, null=True, blank=True)

    def __str__(self):
        return f"TaskResult {self.task_result.task_id} for {self.workspace.source_id} "


class User(AbstractBaseUser, BaseModel, auth_models.PermissionsMixin):
    """User model to work with OIDC only authentication."""

    sub_validator = validators.RegexValidator(
        regex=r"^[\w.@+-]+\Z",
        message=_(
            "Enter a valid sub. This value may contain only letters, "
            "numbers, and @/./+/-/_ characters."
        ),
    )

    sub = models.CharField(
        _("sub"),
        help_text=_(
            "Required. 255 characters or fewer. Letters, numbers, and @/./+/-/_ characters only."
        ),
        max_length=255,
        unique=True,
        validators=[sub_validator],
        blank=True,
        null=True,
    )
    email = models.EmailField(_("identity email address"), blank=True, null=True)

    # Unlike the "email" field which stores the email coming from the OIDC token, this field
    # stores the email used by staff users to login to the admin site
    admin_email = models.EmailField(
        _("admin email address"), unique=True, blank=True, null=True
    )

    language = models.CharField(
        max_length=10,
        choices=lazy(lambda: settings.LANGUAGES, tuple)(),
        default=settings.LANGUAGE_CODE,
        verbose_name=_("language"),
        help_text=_("The language in which the user wants to see the interface."),
    )
    timezone = TimeZoneField(
        choices_display="WITH_GMT_OFFSET",
        use_pytz=False,
        default=settings.TIME_ZONE,
        help_text=_("The timezone in which the user wants to see times."),
    )
    is_device = models.BooleanField(
        _("device"),
        default=False,
        help_text=_("Whether the user is a device or a real user."),
    )
    is_staff = models.BooleanField(
        _("staff status"),
        default=False,
        help_text=_("Whether the user can log into this admin site."),
    )
    is_active = models.BooleanField(
        _("active"),
        default=True,
        help_text=_(
            "Whether this user should be treated as active. "
            "Unselect this instead of deleting accounts."
        ),
    )

    workspaces = models.ManyToManyField(Workspace)

    objects = auth_models.UserManager()

    USERNAME_FIELD = "admin_email"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "main_user"
        verbose_name = _("user")
        verbose_name_plural = _("users")

    def __str__(self):
        return self.email or self.admin_email or str(self.id)

    def email_user(self, subject, message, from_email=None, **kwargs):
        """Email this user."""
        if not self.email:
            raise ValueError("User has no email address.")
        mail.send_mail(subject, message, from_email, [self.email], **kwargs)


class FeatureFlag(models.Model):
    class Name(models.TextChoices):
        ALLOW_NEW_TASKS = "allow-new-tasks"
        READ_ONLY_MODE = "read-only-mode"

    name = models.CharField(
        max_length=32,
        choices=Name.choices,
    )

    is_active = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class ResanaEmailMapping(models.Model):
    domain = models.CharField(max_length=256, unique=True)

    resana_organization_name = models.CharField(
        max_length=256,
    )

    resana_organization_uuid = models.CharField(
        max_length=256,
    )

    def __str__(self):
        return (
            self.domain
            + " -> "
            + self.resana_organization_name
            + "("
            + self.resana_organization_uuid
            + ")"
        )
