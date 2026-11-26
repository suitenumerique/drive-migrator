"""Cache lock for in-flight organization workspace sync (backend-agnostic)."""

import uuid

from django.conf import settings
from django.core.cache import cache


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
