"""
Tenant lifecycle shared by the platform admin API, the migration and scripts:
create a tenant with its vertical's defaults, apply a template, settings updates with audit.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from core.prompts.verticals import get_vertical
from core.repositories import Repos
from core.repositories.base import utcnow
from core.repositories.repos import TenantsStore
from core.tenancy import invalidate
from core.tenancy.ids import new_id
from core.tenancy.models import HoursEntry, TenantConfig, VERTICALS
from database.mongodb import get_db

# Fields an owner/admin may change from the dashboard (top-level keys of the tenant document).
EDITABLE_FIELDS = frozenset({
    "name", "phone", "website", "address", "description", "timezone", "locale", "currency", "country",
    "agent", "hours", "transfer", "emergency_instructions", "notifications", "scheduling", "policies", "hotel",
    "vertical", "onboarding", "links",
})
PLATFORM_FIELDS = frozenset({"status", "limits", "region", "billing", "integrations", "slug"})


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return (s or "business")[:60]


async def unique_slug(name: str) -> str:
    base = slugify(name)
    store = TenantsStore(get_db())
    slug, n = base, 2
    while await store.by_slug(slug):
        slug = f"{base}-{n}"
        n += 1
    return slug


def default_hours_for(vertical: str) -> List[HoursEntry]:
    if vertical == "hotel":
        return [HoursEntry(dow=d, open="00:00", close="24:00") for d in range(7)]
    if vertical == "restaurant":
        return [HoursEntry(dow=d, open="11:30", close="22:00") for d in range(7)]
    if vertical == "salon":
        return [HoursEntry(dow=d, open="09:00", close="18:00") for d in range(1, 6)] + \
               [HoursEntry(dow=5, open="09:00", close="16:00")]
    return [HoursEntry(dow=d, open="08:00", close="17:00") for d in range(5)]


async def apply_template(tenant_id: str, vertical: str, *, replace: bool = False) -> Dict[str, int]:
    """Seed the vertical's example services and FAQs (only where the tenant has none, unless replace)."""
    spec = get_vertical(vertical)
    repos = Repos(get_db(), tenant_id)
    added = {"services": 0, "knowledge": 0}
    if spec.uses_scheduling:
        if replace:
            await repos.services.delete_many({})
        if replace or await repos.services.count() == 0:
            for i, svc in enumerate(spec.default_services):
                await repos.services.insert_one({"_id": new_id("svc"), "active": True, "ai_bookable": True,
                                                 "resource_ids": [], "buffer_min": 0, "sort": i, **svc})
                added["services"] += 1
    if replace or await repos.knowledge.count() == 0:
        for faq in spec.default_faqs:
            await repos.knowledge.insert_one({"_id": new_id("kb"), "question": faq["question"], "answer": faq["answer"],
                                              "tags": [], "active": True, "source": "template", "updated_at": utcnow()})
            added["knowledge"] += 1
    return added


async def create_tenant(*, name: str, vertical: str = "generic", timezone: str = "UTC", country: str = "US",
                        currency: str = "USD", phone: str = "", status: str = "trial", region: str = "us",
                        tenant_id: Optional[str] = None, slug: Optional[str] = None,
                        extra: Optional[Dict[str, Any]] = None, seed_template: bool = True) -> TenantConfig:
    vertical = vertical if vertical in VERTICALS else "generic"
    doc: Dict[str, Any] = {
        "_id": tenant_id or new_id("ten"), "name": name.strip()[:120], "slug": slug or await unique_slug(name),
        "vertical": vertical, "status": status, "region": region, "country": country, "timezone": timezone,
        "currency": currency, "phone": phone,
        "hours": [h.model_dump() for h in default_hours_for(vertical)],
    }
    doc.update(extra or {})
    tenant = TenantConfig.from_doc(doc)                     # validate before writing
    await TenantsStore(get_db()).insert(tenant.to_doc())
    invalidate()
    if seed_template:
        await apply_template(tenant.id, vertical)
    return tenant


def _diff(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    return {k: {"from": before.get(k), "to": after.get(k)} for k in after if before.get(k) != after.get(k)}


async def update_tenant(tenant: TenantConfig, changes: Dict[str, Any], *, allowed: frozenset,
                        user_id: Optional[str], ip: str = "") -> TenantConfig:
    """Validate a partial update by re-building the whole TenantConfig, then write + audit + bust cache."""
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"These fields can't be changed here: {', '.join(sorted(unknown))}")
    before = tenant.to_doc()
    merged = {**before}
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(before.get(key), dict) and key not in ("policies",):
            merged[key] = {**before[key], **value}
        else:
            merged[key] = value
    updated = TenantConfig.from_doc(merged)                 # raises ValueError on bad input
    after = updated.to_doc()
    sets = {k: after[k] for k in changes}
    await TenantsStore(get_db()).update(tenant.id, sets)
    await Repos(get_db(), tenant.id).audit.record(user_id=user_id, action="settings.update", target_type="tenant",
                                                  target_id=tenant.id, diff=_diff({k: before.get(k) for k in sets}, sets),
                                                  ip=ip)
    invalidate(tenant.id)
    updated.updated_at = utcnow()
    return updated
