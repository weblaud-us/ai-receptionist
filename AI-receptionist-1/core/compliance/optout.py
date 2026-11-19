"""
Consent revocation from any channel (Roadmap §11.2–11.3).

The FCC's revocation rules require honouring a "stop" made by any reasonable means. One place
records it, whatever the channel:

    SMS        "STOP" (and the other carrier keywords, or plain words like "stop texting me")
               → POST /telephony/sms/inbound
    WhatsApp   the same words in a chat (whatsapp/meta_handler.py)
    Voice      the sales opt-out listener (core/outbound/optout.py) — writes dnc_list directly
    Dashboard  staff tick "do not contact" on a contact

What an opt-out does:
- every matching contact gets `do_not_contact: true` — in that business, or in every business when
  the message came to a shared platform number (the platform sender texts for all of them);
- platform-level opt-outs (and those to the sales tenant) also go on the internal `dnc_list`, so the
  sales dialer never calls that number (permanent; nothing removes a dnc_list row);
- an append-only `consent_events` row is the evidence (when, which channel, what scope).

"START" / "UNSTOP" re-subscribes texts only: it clears an SMS opt-out on contacts, never dnc_list.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from config import settings
from core.outbound.optout import is_opt_out
from core.phone import digits, mask_phone
from core.tools.hotel import phone_variants

logger = logging.getLogger(__name__)

EVENTS = "consent_events"

# Carrier / CTIA keywords (exact message), plus the FCC's examples of reasonable revocation words.
STOP_WORDS = frozenset({"stop", "stopall", "stop all", "unsubscribe", "cancel", "end", "quit", "revoke", "optout",
                        "opt out", "opt-out", "remove", "stop please", "please stop"})
START_WORDS = frozenset({"start", "unstop", "subscribe"})
HELP_WORDS = frozenset({"help", "info"})
_STOP_TEXT = re.compile(r"\b(stop|quit|cease)\s+(texting|messaging|sending|contacting)\b"
                        r"|\b(don'?t|do\s+not|never)\s+(text|message|contact)\s+(me|us)\b", re.IGNORECASE)

Kind = Literal["stop", "start", "help"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def e164(phone: str) -> str:
    d = digits(phone)
    return f"+{d}" if d else ""


# In a chat these usually mean something else ("cancel" = cancel my booking), so WhatsApp ignores them.
CHAT_AMBIGUOUS = frozenset({"cancel", "end", "quit", "remove"})


def classify(text: str, *, chat: bool = False) -> Optional[Kind]:
    """What an inbound message asks for. Errs towards "stop": a false stop costs one text, a missed one a
    violation. `chat=True` (WhatsApp) skips the one-word keywords that mean something else in a conversation."""
    raw = " ".join((text or "").replace("’", "'").split())
    word = re.sub(r"[^a-z\- ]", "", raw.lower()).strip()
    if word in STOP_WORDS and not (chat and word in CHAT_AMBIGUOUS):
        return "stop"
    if word in START_WORDS:
        return "start"
    if word in HELP_WORDS:
        return "help"
    if _STOP_TEXT.search(raw) or is_opt_out(raw):
        return "stop"
    return None


def _platform_scope(tenant_id: Optional[str]) -> bool:
    return tenant_id is None or (bool(settings.SALES_TENANT_ID) and tenant_id == settings.SALES_TENANT_ID)


async def record_opt_out(db: Any, phone: str, *, channel: str, source: str = "", tenant_id: Optional[str] = None,
                         note: str = "", now: Optional[datetime] = None) -> Dict[str, Any]:
    """Immediate. Returns what changed. `tenant_id=None` = the platform (every business, plus dnc_list)."""
    now = now or utcnow()
    number = e164(phone)
    if not number:
        return {"recorded": False}
    q: Dict[str, Any] = {"phone": {"$in": phone_variants(number)}}
    if tenant_id:
        q["tenant_id"] = tenant_id
    res = await db.contacts.update_many(q, {"$set": {"do_not_contact": True, "do_not_contact_at": now,
                                                     "do_not_contact_source": channel[:20]}})
    dnc_added = False
    if _platform_scope(tenant_id):
        from core.outbound.registry import add_to_dnc

        dnc_added = await add_to_dnc(db, number, reason=f"{channel}_opt_out", source=channel, note=note, now=now)
    scope = "platform" if tenant_id is None else "tenant"
    await db[EVENTS].insert_one({"kind": "opt_out", "phone": number, "channel": channel[:20], "source": source[:60],
                                 "tenant_id": tenant_id, "scope": scope, "note": note[:300], "at": now,
                                 "contacts_updated": int(getattr(res, "modified_count", 0) or 0),
                                 "dnc_added": dnc_added})
    logger.info("Opt-out from %s via %s (%s scope)", mask_phone(number), channel, scope)
    return {"recorded": True, "scope": scope, "contacts_updated": int(getattr(res, "modified_count", 0) or 0),
            "dnc_added": dnc_added}


async def record_opt_in(db: Any, phone: str, *, channel: str, tenant_id: Optional[str] = None,
                        now: Optional[datetime] = None) -> Dict[str, Any]:
    """START: texts may resume. Only undoes opt-outs made on the same channel; dnc_list stays."""
    now = now or utcnow()
    number = e164(phone)
    if not number:
        return {"recorded": False}
    q: Dict[str, Any] = {"phone": {"$in": phone_variants(number)}, "do_not_contact_source": channel[:20]}
    if tenant_id:
        q["tenant_id"] = tenant_id
    res = await db.contacts.update_many(q, {"$set": {"do_not_contact": False},
                                            "$unset": {"do_not_contact_at": "", "do_not_contact_source": ""}})
    await db[EVENTS].insert_one({"kind": "opt_in", "phone": number, "channel": channel[:20], "tenant_id": tenant_id,
                                 "scope": "platform" if tenant_id is None else "tenant", "at": now,
                                 "contacts_updated": int(getattr(res, "modified_count", 0) or 0)})
    return {"recorded": True, "contacts_updated": int(getattr(res, "modified_count", 0) or 0)}


async def is_opted_out(db: Any, tenant_id: str, phone: str) -> bool:
    number = e164(phone)
    if not number:
        return False
    doc = await db.contacts.find_one({"tenant_id": tenant_id, "phone": {"$in": phone_variants(number)},
                                      "do_not_contact": True}, {"_id": 1})
    return doc is not None


def reply_for(kind: Kind, business: str = "") -> str:
    who = business or "this number"
    if kind == "stop":
        return f"You're unsubscribed from texts from {who}. No more messages will be sent. Reply START to resubscribe."
    if kind == "start":
        return f"You're resubscribed to texts from {who}. Reply STOP to unsubscribe."
    support = f" Help: {settings.SUPPORT_EMAIL}." if settings.SUPPORT_EMAIL else ""
    return f"Appointment and message updates from {who}. Reply STOP to unsubscribe.{support}"
