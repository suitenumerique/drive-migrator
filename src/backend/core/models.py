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


# TODO: Add meta data field a jsonb
class Workspace(BaseModel):
    class Status(models.TextChoices):
        NONE = "NONE"
        PENDING = "PENDING"
        FAILURE = "FAILURE"
        SUCCESS = "SUCCESS"

    osmose_id = models.CharField(help_text=_("id of the Osmose workspace"), unique=True)

    resana_id = models.CharField(
        help_text=_("id of the Resana workspace"), null=True, blank=True
    )

    resana_job_id = models.CharField(
        help_text=_("id of the Resana job"), null=True, blank=True
    )

    migration_user = models.ForeignKey("User", models.SET_NULL, blank=True, null=True)

    title = models.CharField()

    """
    Do not edit this field directly. Use status_archive and status_resana instead.
    """
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.NONE,
    )

    """
    Do not update this field directly. Use set_status_archive instead.
    """
    status_archive = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.NONE,
    )

    """
    Do not update this field directly. Use set_status_resana instead.
    """
    status_resana = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.NONE,
    )

    def set_status_archive(self, status):
        self.status_archive = status
        self.sync_status()

    def set_status_resana(self, status):
        self.status_resana = status
        self.sync_status()

    def sync_status(self):
        self.status = self.compute_status()

    def compute_status(self):
        # +---------+---------+---------+---------+---------+
        # | Status  |  NONE   | PENDING | FAILURE | SUCCESS |
        # +---------+---------+---------+---------+---------+
        # | NONE    | NONE    | PENDING | FAILURE | SUCCESS |
        # | PENDING | PENDING | PENDING | PENDING | PENDING |
        # | FAILURE | FAILURE | PENDING | FAILURE | FAILURE |
        # | SUCCESS | SUCCESS | PENDING | FAILURE | SUCCESS |

        if (
            self.status_archive == Workspace.Status.NONE
            and self.status_resana == Workspace.Status.NONE
        ):
            return Workspace.Status.NONE

        if Workspace.Status.PENDING in (self.status_archive, self.status_resana):
            return Workspace.Status.PENDING

        if Workspace.Status.FAILURE in (self.status_archive, self.status_resana):
            return Workspace.Status.FAILURE

        return Workspace.Status.SUCCESS


class ExtraTaskInfo(models.Model):
    task_result = models.OneToOneField(
        TaskResult,
        on_delete=models.CASCADE,
    )

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)

    # Should not be null, but we need to allow it for the initial migration.
    user = models.ForeignKey("User", on_delete=models.CASCADE, null=True, blank=True)

    def __str__(self):
        return f"TaskResult {self.task_result.task_id} for {self.workspace.osmose_id} "


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
