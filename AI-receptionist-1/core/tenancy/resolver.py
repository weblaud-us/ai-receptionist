"""
Tenant resolution (Roadmap Task 2.4) — who does this call / message / request belong to?

| Entry point          | Key                               | Lookup                                   |
|----------------------|-----------------------------------|------------------------------------------|
| Inbound phone call   | sip.trunkPhoneNumber (dialled)    | phone_numbers.e164 → tenant_id           |
| WhatsApp webhook     | metadata.phone_number_id          | tenants.integrations.whatsapp.phone_number_id |
| Dashboard API        | JWT → membership                  | tenants._id                              |
| Public links         | slug / signed id                  | tenants.slug                             |

Results are cached in-process for CACHE_TTL_S; a settings change calls `invalidate()` so the
*next* call sees it. A call never changes tenant mid-flight.

Legacy mode: until the `tenants` collection has a document, every lookup returns the hotel
built from `HOTEL_*` settings (`legacy_hotel_tenant`). After migration m0003 an unknown number
returns None and the caller hears a neutral "not set up yet" message instead of a crash.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from core.phone import digits
from core.tenancy.models import LEGACY_TENANT_ID, TenantConfig, legacy_hotel_tenant
from database.mongodb import get_db

logger = logging.getLogger(__name__)

CACHE_TTL_S = 60.0
_LEGACY_CHECK_TTL_S = 30.0

_cache: Dict[Tuple[str, str], Tuple[float, Optional[TenantConfig]]] = {}
_legacy_mode: Tuple[float, Optional[bool]] = (0.0, None)


def _now() -> float:
    return time.monotonic()


def invalidate(tenant_id: Optional[str] = None) -> None:
    """Drop cached lookups (all, or those that resolved to one tenant)."""
    global _legacy_mode
    _legacy_mode = (0.0, None)
    if tenant_id is None:
        _cache.clear()
        return
    for key, (_, t) in list(_cache.items()):
        if t is not None and t.id == tenant_id:
            _cache.pop(key, None)


async def legacy_mode() -> bool:
    """True while no tenant has been created (the .env hotel is the only business)."""
    global _legacy_mode
    at, value = _legacy_mode
    if value is not None and _now() - at < _LEGACY_CHECK_TTL_S:
        return value
    try:
        value = (await get_db().tenants.find_one({}, {"_id": 1})) is None
    except Exception:
        logger.warning("tenants lookup failed; assuming legacy single-tenant mode", exc_info=True)
        value = True
    _legacy_mode = (_now(), value)
    return value


async def _cached(kind: str, key: str, fetch: Callable[[], Awaitable[Optional[TenantConfig]]]) -> Optional[TenantConfig]:
    hit = _cache.get((kind, key))
    if hit and _now() - hit[0] < CACHE_TTL_S:
        return hit[1]
    tenant = await fetch()
    _cache[(kind, key)] = (_now(), tenant)
    return tenant


def _load(doc: Optional[Dict[str, Any]]) -> Optional[TenantConfig]:
    if not doc:
        return None
    try:
        return TenantConfig.from_doc(doc)
    except Exception:
        logger.error("Invalid tenant document %s", doc.get("_id"), exc_info=True)
        return None


async def resolve_by_id(tenant_id: str) -> Optional[TenantConfig]:
    if not tenant_id:
        return None
    if tenant_id == LEGACY_TENANT_ID and await legacy_mode():
        return legacy_hotel_tenant()

    async def fetch() -> Optional[TenantConfig]:
        return _load(await get_db().tenants.find_one({"_id": tenant_id}))

    return await _cached("id", tenant_id, fetch)


async def resolve_by_slug(slug: str) -> Optional[TenantConfig]:
    if not slug:
        return None

    async def fetch() -> Optional[TenantConfig]:
        return _load(await get_db().tenants.find_one({"slug": slug.lower()}))

    return await _cached("slug", slug.lower(), fetch)


async def resolve_by_phone_number(e164: Optional[str]) -> Optional[TenantConfig]:
    """The tenant that owns a dialled number (inbound calls). None = unknown number."""
    if await legacy_mode():
        return legacy_hotel_tenant()
    key = "+" + digits(e164) if e164 else ""
    if not key or key == "+":
        return None

    async def fetch() -> Optional[TenantConfig]:
        db = get_db()
        row = await db.phone_numbers.find_one({"e164": key, "status": {"$ne": "released"}}, {"tenant_id": 1})
        if not row:
            return None
        return _load(await db.tenants.find_one({"_id": row["tenant_id"]}))

    return await _cached("phone", key, fetch)


async def resolve_by_whatsapp_phone_number_id(phone_number_id: Optional[str]) -> Optional[TenantConfig]:
    """The tenant whose WhatsApp Business number received a message."""
    if await legacy_mode():
        return legacy_hotel_tenant()
    if not phone_number_id:
        return None

    async def fetch() -> Optional[TenantConfig]:
        return _load(await get_db().tenants.find_one(
            {"integrations.whatsapp.phone_number_id": str(phone_number_id)}))

    return await _cached("wa", str(phone_number_id), fetch)
