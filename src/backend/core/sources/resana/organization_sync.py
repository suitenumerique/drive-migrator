"""Synchronize Resana organizations this user administers."""

import html

from django.conf import settings
from django.db import transaction

from celery.utils.log import get_task_logger

from core.models import Organization, User
from core.sources.resana.resana_members_client import ResanaMembersClient
from core.sources.resana.token_manager import ResanaTokenManager

logger = get_task_logger(__name__)


def sync_administered_organizations(user_id) -> None:
    """Upsert the organizations `user_id` administers and drop the stale links.

    The organization rows themselves stay: another administrator, or workspaces,
    may still reference them.
    """
    user = User.objects.get(id=user_id)
    entries = _portal_client(user).get_administered_organizations()
    kept = []
    with transaction.atomic():
        for entry in entries:
            source_id = _entry_id(entry)
            if source_id is None:
                continue
            organization = Organization.for_external_id(
                source_id,
                _organization_name(entry, ""),
                from_portal=True,
            )
            organization.admins.add(user)
            kept.append(organization.pk)
        stale = list(user.administered_organizations.exclude(pk__in=kept))
        for organization in stale:
            organization.admins.remove(user)
    logger.info("Synced %s administered organizations for user %s", len(kept), user_id)


def _portal_client(user) -> ResanaMembersClient:
    """Refresh the portal session, then return a client bound to it."""
    manager = ResanaTokenManager(user)
    manager.ensure_offline_token()
    return ResanaMembersClient(
        access_token=manager.get_valid_token(),
        session_id=manager.get_session_id(),
        csrf_token=manager.get_csrf_token(),
        base_url=settings.RESANA_WEB_ENDPOINT,
    )


def _entry_id(entry: dict) -> str | None:
    raw = entry.get("id")
    if raw in (None, ""):
        logger.warning("Skipping a portal entry with no id")
        return None
    return str(raw)


def _organization_name(entry: dict, fallback: str) -> str:
    name = entry.get("name")
    if not name:
        return fallback
    return html.unescape(str(name))