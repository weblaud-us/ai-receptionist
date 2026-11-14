"""
Business profile helpers.

Phase 2 moved the business into the `tenants` collection (`core.tenancy`). This module keeps the
flat profile dict the pre-Phase-2 hotel modules (WhatsApp templates, reminders, calendar links)
still read, now derived from a `TenantConfig`:

    tenant_profile(tenant)   # any tenant -> flat dict
    get_hotel_profile()      # the legacy .env hotel (used only until migration m0003 has run)
"""

import logging
from typing import Any, Dict, Optional

from config import settings
from core.tenancy.models import TenantConfig, legacy_hotel_tenant

logger = logging.getLogger(__name__)


def tenant_profile(t: TenantConfig) -> Dict[str, Any]:
    """Flat, template-friendly view of a tenant (keys kept from the single-hotel era)."""
    return {
        "hotel_id":                      t.id,
        "tenant_id":                     t.id,
        "name":                          t.name,
        "receptionist_name":             t.agent.name,
        "phone":                         t.phone,
        "address":                       t.address_text,
        "checkin_time":                  t.hotel.checkin_time,
        "checkout_time":                 t.hotel.checkout_time,
        "currency":                      t.currency,
        "timezone":                      t.timezone,
        "vertical":                      t.vertical,
        "twilio_voice_number":           settings.TWILIO_PHONE_NUMBER if t.is_legacy else "",
        "meta_whatsapp_phone_number_id": t.integrations.whatsapp.phone_number_id or settings.META_WA_PHONE_NUMBER_ID,
        "meta_waba_id":                  t.integrations.whatsapp.waba_id or settings.META_WABA_ID,
        "db_name":                       settings.MONGODB_DB_NAME,
        "is_active":                     t.active,
    }


def get_hotel_profile() -> Dict[str, Any]:
    """The legacy single hotel, from settings. New code resolves a tenant instead."""
    return tenant_profile(legacy_hotel_tenant())


# -- Compatibility stubs kept for old imports ---------------------------------

def set_current_tenant(hotel_doc=None, *args, **kwargs):
    pass


def clear_current_tenant() -> None:
    pass


def get_current_hotel() -> Dict[str, Any]:
    return get_hotel_profile()


async def resolve_hotel_by_twilio_number(to_number: Optional[str] = None, channel: str = "voice") -> Dict[str, Any]:
    from core.tenancy.resolver import resolve_by_phone_number
    t = await resolve_by_phone_number(to_number)
    return tenant_profile(t or legacy_hotel_tenant())


async def resolve_hotel_by_meta_phone_number_id(phone_number_id: Optional[str] = None) -> Dict[str, Any]:
    from core.tenancy.resolver import resolve_by_whatsapp_phone_number_id
    t = await resolve_by_whatsapp_phone_number_id(phone_number_id)
    return tenant_profile(t or legacy_hotel_tenant())


async def resolve_hotel_by_id(hotel_id: Optional[str] = None) -> Dict[str, Any]:
    from core.tenancy.resolver import resolve_by_id
    t = await resolve_by_id(hotel_id or "default")
    return tenant_profile(t or legacy_hotel_tenant())
