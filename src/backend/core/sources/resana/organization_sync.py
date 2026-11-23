"""Synchronize Resana organizations and their workspaces into the local database.

Organizations come from the portal's getPerimetre (numeric ids, no UUID).
Workspaces of an organization come from gererPerimetresInformations, which also
has no GED UUID: they are stored under portal_id, and source_id is filled with
portal:<id> only when it is still empty.
"""

import html
import uuid

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from celery.utils.log import get_task_logger

from core.models import Organization, User, Workspace
from core.sources.resana.resana_members_client import ResanaMembersClient
from core.sources.resana.token_manager import ResanaTokenManager

logger = get_task_logger(__name__)

# Distinct from a GED uuid (02-01-...), so a later animator sync can keep its own row.
PORTAL_SOURCE_PREFIX = "portal:"


def organization_sync_lock_key(source_id: str) -> str:
    """Cache key that holds the in-flight workspace sync of one organization."""
    return f"admin-org-sync:{source_id}"


def claim_organization_sync(source_id: str) -> tuple[str, bool]:
    """Reserve the sync of one organization.

    The first caller receives a new task id and True. A caller that arrives
    while the key is held receives that task id and False.
    """
    task_id = uuid.uuid4().hex
    key = organization_sync_lock_key(source_id)
    ttl = settings.ADMIN_ORG_SYNC_TIMEOUT_SECONDS
    if cache.add(key, task_id, timeout=ttl):
        return task_id, True
    held = cache.get(key)
    if held:
        return held, False
    cache.set(key, task_id, timeout=ttl)
    return task_id, True


def release_organization_sync(source_id: str) -> None:
    """Drop the sync lock. A missing key is not an error."""
    cache.delete(organization_sync_lock_key(source_id))


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


def sync_organization_workspaces(user_id, source_id: str) -> None:
    """Upsert the shared workspaces of one organization and attach them to it.

    Creates the organization when it is missing. Workspaces that disappear from
    the portal are left in place.
    """
    try:
        user = User.objects.get(id=user_id)
        entries = _portal_client(user).get_organization_workspaces(source_id)
        now = timezone.now()
        with transaction.atomic():
            organization = Organization.for_external_id(source_id, "", from_portal=True)
            organization.admins.add(user)
            synced = 0
            for entry in entries:
                if _upsert_portal_workspace(organization, entry) is not None:
                    synced += 1
            organization.last_sync = now
            organization.save()
        logger.info(
            "Synced %s workspaces of organization %s for user %s",
            synced,
            source_id,
            user_id,
        )
    finally:
        release_organization_sync(source_id)


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


def _upsert_portal_workspace(organization, entry: dict) -> Workspace | None:
    portal_id = _entry_id(entry)
    if portal_id is None:
        return None
    title = html.unescape(str(entry.get("nom") or portal_id))
    placeholder = f"{PORTAL_SOURCE_PREFIX}{portal_id}"
    workspace = Workspace.objects.filter(portal_id=portal_id).first()
    if workspace is None:
        workspace = Workspace.objects.filter(source_id=placeholder).first()
    if workspace is None:
        workspace = Workspace(
            portal_id=portal_id,
            source_id=placeholder,
            source_type="resana",
            title=title,
            organization=organization,
        )
        workspace.save()
        return workspace
    workspace.portal_id = portal_id
    workspace.title = title
    workspace.organization = organization
    if not workspace.source_type:
        workspace.source_type = "resana"
    # A GED uuid already stored here must survive this portal sync.
    if not workspace.source_id:
        workspace.source_id = placeholder
    workspace.save()
    return workspace
