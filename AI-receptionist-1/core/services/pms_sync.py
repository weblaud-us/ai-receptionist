"""
Connect, sync and disconnect a dental practice-management system (Cliniko / Dentally).

    await connect(tenant, "cliniko", api_key, user_id=...)   # test the key, store it encrypted, sync
    await sync(tenant)                                        # practitioners → resources, types → services
    await disconnect(tenant)

Mirroring: each practitioner becomes a `resources` row and each Cliniko appointment type a
`services` row, both marked `source: <provider>` with a `pms_id`. Re-syncs update names and
durations but never overwrite what the owner set locally (price, value, category, AI-bookable).
Anything the PMS no longer lists is deactivated, never deleted (past appointments point at it).

Runs from the dashboard (on demand) and from services/tenant_jobs (every few hours). Never during
a call.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from core import crypto
from core.repositories import Repos
from core.repositories.repos import TenantsStore
from core.services import scheduling_provider as sp
from core.tenancy import invalidate
from core.tenancy.ids import new_id
from core.tenancy.models import TenantConfig
from database.mongodb import get_db

logger = logging.getLogger(__name__)

SYNC_EVERY_S = 6 * 3600


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _set(tenant_id: str, **fields: Any) -> None:
    await TenantsStore(get_db()).update(tenant_id, {f"integrations.pms.{k}": v for k, v in fields.items()})
    invalidate(tenant_id)


def available_providers() -> Dict[str, bool]:
    from config import settings

    return {"cliniko": True, "dentally": bool(settings.PMS_DENTALLY_ENABLED)}


async def connect(tenant: TenantConfig, provider: str, secret: str, *, user_id: str) -> Dict[str, Any]:
    """Check the credentials against the PMS, then store them (encrypted) and run a first sync.
    Raises ValueError for a bad provider and sp.PMSError when the PMS refuses."""
    if provider not in ("cliniko", "dentally") or not available_providers().get(provider):
        raise ValueError(f"{provider} isn't available")
    secret = (secret or "").strip()
    if len(secret) < 10:
        raise ValueError("That key looks too short")
    client = sp.build_provider(provider, secret, {}, tenant)
    info = await client.test_connection()
    ids = {k: info[k] for k in ("business_id", "site_id") if info.get(k)}
    await TenantsStore(get_db()).update(tenant.id, {"integrations.pms": {
        "provider": provider, "connected": True, "api_key_enc": crypto.encrypt(secret),
        "connected_by": user_id, "connected_at": _utcnow(), "last_error": None, "last_sync_at": None,
        "account": {k: v for k, v in info.items() if k not in ("business_id", "site_id")}, **ids,
    }})
    invalidate(tenant.id)
    from core.tenancy import resolve_by_id

    fresh = await resolve_by_id(tenant.id) or tenant
    stats = await sync(fresh)
    return {"provider": provider, **info, "sync": stats}


async def sync(tenant: TenantConfig) -> Dict[str, int]:
    """Mirror practitioners and appointment types. Never raises; errors are stored on the tenant."""
    provider = sp.provider_for(tenant)
    if provider is None:
        return {"practitioners": 0, "types": 0}
    repos = Repos(get_db(), tenant.id)
    try:
        practitioners = await provider.list_practitioners()
        types = await provider.list_appointment_types()
    except sp.PMSError as e:
        logger.warning("PMS sync for %s failed: %s", tenant.id, e)
        await _set(tenant.id, last_error=str(e)[:200], last_error_at=_utcnow())
        return {"practitioners": 0, "types": 0, "error": 1}
    now = _utcnow()
    seen_res = []
    for p in practitioners:
        seen_res.append(p.id)
        await repos.resources.update_one(
            {"source": provider.name, "pms_id": p.id},
            {"$set": {"name": p.name[:120], "active": p.active, "updated_at": now},
             "$setOnInsert": {"_id": new_id("res"), "kind": "staff", "hours": None, "capacity": 1, "sort": 0}},
            upsert=True)
    await repos.resources.update_many({"source": provider.name, "pms_id": {"$nin": seen_res}},
                                      {"$set": {"active": False, "updated_at": now}})
    seen_types = []
    for at in types:
        seen_types.append(at.id)
        await repos.services.update_one(
            {"source": provider.name, "pms_id": at.id},
            {"$set": {"name": at.name[:120], "duration_min": max(5, min(600, at.duration_min)), "active": at.active,
                      "updated_at": now},
             "$setOnInsert": {"_id": new_id("svc"), "buffer_min": 0, "resource_ids": [], "ai_bookable": True,
                              "description": "", "sort": 0, "price": None, "estimated_value_minor": None,
                              "category": None}},
            upsert=True)
    if provider.requires_type_mapping:
        await repos.services.update_many({"source": provider.name, "pms_id": {"$nin": seen_types}},
                                         {"$set": {"active": False, "updated_at": now}})
    await _set(tenant.id, last_sync_at=now, last_error=None)
    logger.info("PMS sync for %s: %d practitioners, %d appointment types", tenant.id, len(practitioners), len(types))
    return {"practitioners": len(practitioners), "types": len(types)}


async def test(tenant: TenantConfig) -> Dict[str, Any]:
    provider = sp.provider_for(tenant)
    if provider is None:
        raise ValueError("No practice-management system is connected")
    return await provider.test_connection()


async def disconnect(tenant: TenantConfig) -> None:
    """Forget the credentials. Mirrored dentists are deactivated, because without the PMS their diaries
    are unknown. Mirrored services stay (with the owner's prices and values) but are no longer linked."""
    name = sp.pms_settings(tenant).get("provider")
    await TenantsStore(get_db()).update(tenant.id, {"integrations.pms": {"provider": "builtin", "connected": False,
                                                                         "disconnected_at": _utcnow()}})
    if name in ("cliniko", "dentally"):
        await Repos(get_db(), tenant.id).resources.update_many({"source": name}, {"$set": {"active": False}})
    invalidate(tenant.id)


def due(tenant: TenantConfig, now: datetime) -> bool:
    if not sp.connected(tenant):
        return False
    last = sp.pms_settings(tenant).get("last_sync_at")
    if isinstance(last, datetime):
        last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
        return (now - last).total_seconds() >= SYNC_EVERY_S
    return True
