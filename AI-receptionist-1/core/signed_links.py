"""
Signed, expiring links for the browser call page (/call/{room}?exp=…&sig=…).

The WhatsApp "call me" flow sends a guest a link. Only someone holding that link — and only
until it expires — can get a LiveKit token for that one room.
"""

import hashlib
import hmac
import time
from typing import Optional

from config import settings

CALL_LINK_TTL_SECONDS = 15 * 60


def _sig(room: str, exp: int, tenant_id: Optional[str] = None) -> str:
    key = settings.SECRET_KEY.encode("utf-8")
    msg = f"call:{room}:{exp}" + (f":{tenant_id}" if tenant_id else "")
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def sign_call_room(room: str, ttl_seconds: int = CALL_LINK_TTL_SECONDS, now: Optional[float] = None,
                   tenant_id: Optional[str] = None) -> tuple[int, str]:
    exp = int(now if now is not None else time.time()) + ttl_seconds
    return exp, _sig(room, exp, tenant_id)


def verify_call_room(room: str, exp: Optional[int], sig: Optional[str], now: Optional[float] = None,
                     tenant_id: Optional[str] = None) -> bool:
    """The signature covers the room, the expiry and (Phase 2) the tenant the call is for."""
    if not room or not exp or not sig:
        return False
    if int(exp) < int(now if now is not None else time.time()):
        return False
    return hmac.compare_digest(sig, _sig(room, int(exp), tenant_id or None))


def build_call_link(base_url: str, room: str, tenant_id: Optional[str] = None) -> str:
    exp, sig = sign_call_room(room, tenant_id=tenant_id)
    link = f"{base_url.rstrip('/')}/call/{room}?exp={exp}&sig={sig}"
    return link + (f"&t={tenant_id}" if tenant_id else "")
