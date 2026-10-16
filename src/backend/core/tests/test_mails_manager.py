"""Tests for MailsManager's generic migration mail sender."""

# pylint: disable=no-member
# django.core.mail.outbox is injected dynamically by Django's test runner.

from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core import mail
from django.template.loader import render_to_string
from django.test import override_settings

import pytest

from core.backends.source import SourceManager
from core.mails_manager import MailsManager
from core.models import Workspace


@pytest.mark.django_db
@pytest.mark.parametrize(
    "template_name",
    ["fail", "archive_download", "drive_ready", "resana_ready", "resana_ready_errors"],
)
def test_send_migration_mail_renders_named_template_and_sends(template_name):
    """send_migration_mail() renders mail/html/{template_name}.html and
    mail/text/{template_name}.txt, then sends the resulting email to the user."""
    workspace = MagicMock(spec=Workspace)
    workspace.title = "Équipe <projet> & partenaires"
    user = MagicMock()
    user.email = "user@example.com"

    MailsManager().send_migration_mail(
        user, workspace, template_name, {"title": "Some Title"}
    )

    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.subject == "Some Title"
    assert sent.to == ["user@example.com"]
    assert sent.from_email == settings.EMAIL_FROM
    assert "Équipe" in sent.body
    html = sent.alternatives[0][0]
    assert "Équipe &lt;projet&gt; &amp; partenaires" in html
    for content in (sent.body, html):
        assert "contacter le support" in content
        assert "Ce mail a été envoyé" in content
    assert "data:image/png;base64," in html


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


ALL_TEMPLATES = [
    "fail",
    "archive_download",
    "drive_ready",
    "resana_ready",
    "resana_ready_errors",
]


def _workspace_with_errors():
    return Workspace(
        title="Équipe projet",
        download_errors=[
            {"id": "1", "name": "a.pdf", "path": "Docs/a.pdf", "error": "boom"}
        ],
        upload_errors=[
            {"path": "b.exe", "item_id": "i", "error": "file_type_not_allowed"}
        ],
    )


@pytest.mark.django_db
@pytest.mark.parametrize("template_name", ALL_TEMPLATES)
def test_send_migration_mail_attaches_failed_files_csv(template_name):
    """Every migration mail carries the CSV of the files that failed."""
    workspace = _workspace_with_errors()
    user = MagicMock()
    user.email = "user@example.com"

    MailsManager().send_migration_mail(
        user, workspace, template_name, {"title": "Some Title"}
    )

    sent = mail.outbox[0]
    assert len(sent.attachments) == 1
    filename, content, mimetype = sent.attachments[0]
    assert filename == "fichiers-non-migres-equipe-projet.csv"
    assert mimetype == "text/csv"
    assert content == workspace.get_failed_files_csv(
        SourceManager().get_backend().label
    )
    for body in (sent.body, sent.alternatives[0][0]):
        assert "2 fichiers n'ont pas pu être migrés" in body
        assert "fichier joint" in body


@pytest.mark.django_db
def test_send_migration_mail_uses_singular_for_one_failed_file():
    """The mail sentence agrees with a single failed file."""
    workspace = Workspace(
        title="Équipe projet",
        download_errors=[{"id": "1", "name": "a.pdf", "path": "a.pdf", "error": "x"}],
        upload_errors=[],
    )
    user = MagicMock()
    user.email = "user@example.com"

    MailsManager().send_migration_mail(
        user, workspace, "drive_ready", {"title": "Some Title"}
    )

    assert "1 fichier n'a pas pu être migré" in mail.outbox[0].body


@pytest.mark.django_db
@pytest.mark.parametrize("template_name", ALL_TEMPLATES)
def test_send_migration_mail_has_no_attachment_without_failed_files(template_name):
    """No attachment and no failed-files sentence when every file was migrated."""
    workspace = Workspace(title="Équipe projet", download_errors=[], upload_errors=[])
    user = MagicMock()
    user.email = "user@example.com"

    MailsManager().send_migration_mail(
        user, workspace, template_name, {"title": "Some Title"}
    )

    sent = mail.outbox[0]
    assert sent.attachments == []
    for body in (sent.body, sent.alternatives[0][0]):
        assert "fichier joint" not in body


@pytest.mark.django_db
@pytest.mark.parametrize("template_name", ALL_TEMPLATES)
@override_settings(LOGIN_REDIRECT_URL="http://localhost:3010")
def test_send_migration_mail_links_the_rejected_files_archive(template_name):
    """A destination that kept its refused files in a zip gets a link to the
    frontend page that downloads it, never the raw S3 URL."""
    workspace = _workspace_with_errors()
    workspace.destination_metadata = {
        "drive": {"workspace_id": "root", "rejected_files_s3_key": "ws.zip"},
        "archive": {"s3_key": "workspace.zip"},
    }
    user = MagicMock()
    user.email = "user@example.com"

    MailsManager().send_migration_mail(
        user, workspace, template_name, {"title": "Some Title"}
    )

    url = (
        f"http://localhost:3010/download-rejected-files/?workspaceId={workspace.id}"
        "&destination=drive"
    )
    sent = mail.outbox[0]
    for body in (sent.body, sent.alternatives[0][0]):
        assert url in body
        assert "refusés par La Suite Drive" in body
        assert "ws.zip" not in body
        assert body.count("download-rejected-files") == 1


@pytest.mark.django_db
@override_settings(LOGIN_REDIRECT_URL="http://localhost:3010")
def test_send_migration_mail_links_the_zip_of_an_unregistered_destination():
    """A destination no longer configured is named after its slug instead of
    preventing the mail from being sent."""
    workspace = _workspace_with_errors()
    workspace.destination_metadata = {"gone": {"rejected_files_s3_key": "ws.zip"}}
    user = MagicMock()
    user.email = "user@example.com"

    MailsManager().send_migration_mail(
        user, workspace, ALL_TEMPLATES[0], {"title": "Some Title"}
    )

    assert "refusés par gone" in mail.outbox[0].body


@pytest.mark.django_db
@pytest.mark.parametrize("template_name", ALL_TEMPLATES)
def test_send_migration_mail_has_no_archive_link_without_zip(template_name):
    """No link when no destination kept a zip of refused files."""
    workspace = _workspace_with_errors()
    workspace.destination_metadata = {"drive": {"workspace_id": "root"}}
    user = MagicMock()
    user.email = "user@example.com"

    MailsManager().send_migration_mail(
        user, workspace, template_name, {"title": "Some Title"}
    )

    sent = mail.outbox[0]
    for body in (sent.body, sent.alternatives[0][0]):
        assert "download-rejected-files" not in body
