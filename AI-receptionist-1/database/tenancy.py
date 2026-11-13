"""
Single-tenant hotel context.
All configuration is read directly from .env via settings — no database lookup needed.
Multi-tenant stubs are kept so existing imports across voice/whatsapp handlers
continue to work without modification.
"""

import logging
from typing import Optional, Dict, Any

from config import settings

logger = logging.getLogger(__name__)


def get_hotel_profile() -> Dict[str, Any]:
    """Return the single hotel's configuration from settings."""
    return {
        "hotel_id":                    "default",
        "name":                        settings.HOTEL_NAME,
        "receptionist_name":           settings.RECEPTIONIST_NAME,
        "phone":                       settings.HOTEL_PHONE,
        "address":                     settings.HOTEL_ADDRESS,
        "checkin_time":                settings.HOTEL_CHECKIN_TIME,
        "checkout_time":               settings.HOTEL_CHECKOUT_TIME,
        "currency":                    settings.HOTEL_CURRENCY,
        "timezone":                    settings.HOTEL_TIMEZONE,
        "twilio_voice_number":         settings.TWILIO_PHONE_NUMBER,
        "twilio_account_sid":          settings.TWILIO_ACCOUNT_SID,
        "twilio_auth_token":           settings.TWILIO_AUTH_TOKEN,
        "meta_whatsapp_phone_number_id": settings.META_WA_PHONE_NUMBER_ID,
        "meta_waba_id":                settings.META_WABA_ID,
        "db_name":                     settings.MONGODB_DB_NAME,
        "is_active":                   True,
    }


# ── Compatibility stubs (single-tenant no-ops) ────────────────────────────────

def set_current_tenant(hotel_doc=None, *args, **kwargs):
    """No-op in single-tenant mode."""
    pass


def clear_current_tenant() -> None:
    """No-op in single-tenant mode."""
    pass


def get_current_hotel() -> Dict[str, Any]:
    """Always returns the single hotel profile."""
    return get_hotel_profile()


async def resolve_hotel_by_twilio_number(
    to_number: Optional[str] = None,
    channel: str = "voice",
) -> Dict[str, Any]:
    """Single-tenant: always return the default hotel."""
    return get_hotel_profile()


async def resolve_hotel_by_meta_phone_number_id(
    phone_number_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Single-tenant: always return the default hotel."""
    return get_hotel_profile()


async def resolve_hotel_by_id(hotel_id: Optional[str] = None) -> Dict[str, Any]:
    """Single-tenant: always return the default hotel."""
    return get_hotel_profile()
