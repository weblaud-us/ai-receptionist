"""
Tenancy (Roadmap Phase 2): the business a request, call or message belongs to.

    from core.tenancy import TenantConfig, resolve_by_phone_number, resolve_by_id

Every entry point resolves a `TenantConfig` once (phone number → tenant, WhatsApp number ID →
tenant, dashboard JWT → tenant) and passes it down. Nothing below the entry points reads the
business profile from settings any more.
"""

from core.tenancy.ids import new_id
from core.tenancy.models import (
    AgentConfig,
    HoursEntry,
    NotificationsConfig,
    TenantConfig,
    TransferConfig,
    VERTICALS,
    legacy_hotel_tenant,
)
from core.tenancy.resolver import (
    invalidate,
    resolve_by_id,
    resolve_by_phone_number,
    resolve_by_slug,
    resolve_by_whatsapp_phone_number_id,
)

__all__ = [
    "AgentConfig", "HoursEntry", "NotificationsConfig", "TenantConfig", "TransferConfig", "VERTICALS",
    "legacy_hotel_tenant", "new_id", "invalidate", "resolve_by_id", "resolve_by_phone_number",
    "resolve_by_slug", "resolve_by_whatsapp_phone_number_id",
]
