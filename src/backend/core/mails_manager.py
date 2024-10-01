from django.contrib.sites.models import Site
from django.core import mail
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _
from django.conf import settings

from core.models import Workspace


class MailsManager:

    def get_recipients(self, user):
        if settings.APP_EMAIL_FORCE_TO:
            return [settings.APP_EMAIL_FORCE_TO]
        return [user.email]

    def send_archive_download_mail(self, user, workspace: Workspace, archive_url):
        title = _(
            "Votre archive de l'espace %(title)s est prête !"
            % {"title": workspace.title}
        )
        template_vars = {
            "title": title,
            "site": Site.objects.get_current(),
            "email": user.email,
            "workspace_name": workspace.title,
            "download_url": archive_url,
        }
        msg_html = render_to_string("mail/html/archive_download.html", template_vars)
        msg_plain = render_to_string("mail/text/archive_download.txt", template_vars)
        mail.send_mail(
            title,
            msg_plain,
            settings.EMAIL_FROM,
            self.get_recipients(user),
            html_message=msg_html,
            fail_silently=False,
        )

    def send_resana_ready_mail(self, user, workspace: Workspace):
        title = _(
            "Votre espace %(title)s est prêt sur Resana !" % {"title": workspace.title}
        )
        template_vars = {
            "title": title,
            "site": Site.objects.get_current(),
            "email": user.email,
            "workspace_name": workspace.title,
            "url": "https://resana.numerique.gouv.fr/public/",
        }
        msg_html = render_to_string("mail/html/resana_ready.html", template_vars)
        msg_plain = render_to_string("mail/text/resana_ready.txt", template_vars)
        mail.send_mail(
            title,
            msg_plain,
            settings.EMAIL_FROM,
            self.get_recipients(user),
            html_message=msg_html,
            fail_silently=False,
        )
