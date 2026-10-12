from django.conf import settings
from django.contrib.sites.models import Site
from django.core import mail
from django.template.loader import render_to_string
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from celery.utils.log import get_task_logger

from core.backends.source import SourceManager
from core.models import Workspace

logger = get_task_logger(__name__)


class MailsManager:
    def get_recipients(self, user):
        if settings.APP_EMAIL_FORCE_TO:
            return [settings.APP_EMAIL_FORCE_TO]
        return [user.email]

    def send_migration_mail(
        self, user, workspace: Workspace, template_name: str, context: dict
    ):
        """Render and send a migration-related email.

        `template_name` selects mail/html/{template_name}.html and
        mail/text/{template_name}.txt. `context` must include a "title" key
        (used both as the email subject and in the template). Any destination
        backend (archive, resana, drive, ...) can reuse this without duplicating
        the render/send boilerplate.
        """
        title = context["title"]
        logger.info(f"Sending '{template_name}' mail to {user.email}")
        source_label = SourceManager().get_backend().label
        template_vars = {
            "site": Site.objects.get_current(),
            "email": user.email,
            "workspace_name": workspace.title,
            "source_label": source_label,
            "failed_files_count": len(workspace.download_errors)
            + len(workspace.upload_errors),
            **context,
        }
        msg_html = render_to_string(f"mail/html/{template_name}.html", template_vars)
        msg_plain = render_to_string(f"mail/text/{template_name}.txt", template_vars)
        message = mail.EmailMultiAlternatives(
            title,
            msg_plain,
            settings.EMAIL_FROM,
            self.get_recipients(user),
        )
        message.attach_alternative(msg_html, "text/html")
        if failed_files_csv := workspace.get_failed_files_csv(source_label):
            message.attach(
                f"fichiers-non-migres-{slugify(workspace.title)}.csv",
                failed_files_csv,
                "text/csv",
            )
        message.send(fail_silently=False)

    def send_fail_mail(self, user, workspace: Workspace):
        title = _(f"Migration ou export l'espace {workspace.title} échoué")
        self.send_migration_mail(user, workspace, "fail", {"title": title})
