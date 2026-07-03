"""Tests for MailsManager's generic migration mail sender."""

# pylint: disable=no-member
# django.core.mail.outbox is injected dynamically by Django's test runner.

from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core import mail
from django.template.loader import render_to_string

import pytest

from core.backends.source import SourceManager
from core.mails_manager import MailsManager
from core.models import Workspace


@pytest.mark.django_db
def test_send_migration_mail_renders_named_template_and_sends():
    """send_migration_mail() renders mail/html/{template_name}.html and
    mail/text/{template_name}.txt, then sends the resulting email to the user."""
    workspace = MagicMock(spec=Workspace)
    workspace.title = "My Workspace"
    user = MagicMock()
    user.email = "user@example.com"

    MailsManager().send_migration_mail(user, workspace, "fail", {"title": "Some Title"})

    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.subject == "Some Title"
    assert sent.to == ["user@example.com"]
    assert sent.from_email == settings.EMAIL_FROM
    assert "My Workspace" in sent.body


@pytest.mark.django_db
def test_send_migration_mail_injects_source_label_from_configured_source_backend():
    """send_migration_mail() exposes the configured source backend's label as
    'source_label' in the template context, so templates don't hardcode 'Osmose'."""
    workspace = MagicMock(spec=Workspace)
    workspace.title = "My Workspace"
    user = MagicMock()
    user.email = "user@example.com"

    with patch(
        "core.mails_manager.render_to_string", wraps=render_to_string
    ) as mock_render:
        MailsManager().send_migration_mail(
            user, workspace, "fail", {"title": "Some Title"}
        )

    template_vars = mock_render.call_args_list[0].args[1]
    assert template_vars["source_label"] == SourceManager().get_backend().label


@pytest.mark.django_db
def test_send_migration_mail_merges_extra_context_into_template():
    """Extra context passed to send_migration_mail() reaches the template
    (e.g. a download_url or a destination-specific link)."""
    workspace = MagicMock(spec=Workspace)
    workspace.title = "My Workspace"
    user = MagicMock()
    user.email = "user@example.com"

    MailsManager().send_migration_mail(
        user,
        workspace,
        "archive_download",
        {
            "title": "Archive ready",
            "download_url": "http://s3.example.com/archive.zip",
        },
    )

    assert len(mail.outbox) == 1
    assert "http://s3.example.com/archive.zip" in mail.outbox[0].alternatives[0][0]


@pytest.mark.django_db
def test_send_migration_mail_respects_force_to_setting():
    """When APP_EMAIL_FORCE_TO is set, the mail is redirected there instead of
    the user's real address."""
    workspace = MagicMock(spec=Workspace)
    workspace.title = "My Workspace"
    user = MagicMock()
    user.email = "user@example.com"

    original = settings.APP_EMAIL_FORCE_TO
    settings.APP_EMAIL_FORCE_TO = "forced@example.com"
    try:
        MailsManager().send_migration_mail(
            user, workspace, "fail", {"title": "Some Title"}
        )
    finally:
        settings.APP_EMAIL_FORCE_TO = original

    assert mail.outbox[0].to == ["forced@example.com"]
