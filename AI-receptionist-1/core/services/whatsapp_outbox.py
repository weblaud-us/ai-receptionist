"""
Staff → patient WhatsApp from the dashboard (patient profile).

- The 24-hour window is tracked on the contact (`wa_last_inbound_at`, set by the Meta webhook on
  every inbound message). Inside it free text is allowed; outside it only approved templates.
- Each send is stored in `wa_outbox`; Meta's later delivery statuses (sent/delivered/read/failed)
  update the row, so a message that was accepted but never arrived shows as failed with the reason.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from core.integrations import whatsapp as wa
from core.repositories import Repos
from core.tenancy.ids import new_id
from core.tenancy.models import TenantConfig
from core.tools.hotel import phone_variants

logger = logging.getLogger(__name__)

# Meta reports statuses in this order; never move a row backwards (webhooks can arrive out of order).
_RANK = {"accepted": 0, "sent": 1, "delivered": 2, "read": 3, "failed": 4}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Any) -> Optional[datetime]:
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def window(contact: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Is the 24-hour customer-service window open for this contact?"""
    last = _aware(contact.get("wa_last_inbound_at"))
    if last is None:
        return {"open": False, "last_inbound_at": None, "expires_at": None}
    expires = last + timedelta(hours=wa.WINDOW_HOURS)
    return {"open": (now or utcnow()) < expires, "last_inbound_at": last, "expires_at": expires}


def tenant_sender(tenant: TenantConfig) -> Dict[str, Any]:
    own = (tenant.integrations.whatsapp.phone_number_id or "").strip()
    return {"phone_number_id": own or None, "waba_id": (tenant.integrations.whatsapp.waba_id or "").strip() or None,
            "configured": wa.configured(own or None), "own_number": bool(own)}


async def note_inbound(repos: Repos, phone: str, at: Optional[datetime] = None, name: Optional[str] = None) -> None:
    """A customer messaged the business: (re)open their 24-hour window. Never raises."""
    if not phone:
        return
    try:
        at = at or utcnow()
        existing = await repos.contacts.find_one({"phone": {"$in": phone_variants(phone)}}, {"_id": 1})
        if existing:
            await repos.contacts.update_one({"_id": existing["_id"]}, {"$set": {"wa_last_inbound_at": at,
                                                                               "last_seen_at": at}})
        else:
            await repos.contacts.upsert_by_phone(phone, {"name": name})
            await repos.contacts.update_one({"phone": phone}, {"$set": {"wa_last_inbound_at": at}})
    except Exception:
        logger.debug("Could not record WhatsApp inbound time", exc_info=True)


async def apply_statuses(repos: Repos, statuses: List[Dict[str, Any]]) -> int:
    """Meta webhook `value.statuses` → update matching outbox rows. Returns rows updated. Never raises."""
    updated = 0
    for st in statuses or []:
        try:
            mid, status = st.get("id"), str(st.get("status") or "")
            if not mid or status not in _RANK:
                continue
            row = await repos.wa_outbox.find_one({"message_id": mid}, {"status": 1})
            if not row or _RANK.get(row.get("status", "accepted"), 0) >= _RANK[status]:
                continue
            sets: Dict[str, Any] = {"status": status, f"{status}_at": utcnow()}
            if status == "failed":
                err = (st.get("errors") or [{}])[0]
                code = err.get("code")
                code = int(code) if str(code).isdigit() else None
                detail = (err.get("error_data") or {}).get("details") or err.get("title") or err.get("message") or ""
                sets.update({"error_code": code, "error": wa.explain(code, detail or "WhatsApp couldn't deliver it.")})
            await repos.wa_outbox.update_one({"_id": row["_id"]}, {"$set": sets})
            updated += 1
        except Exception:
            logger.debug("WhatsApp status update failed", exc_info=True)
    return updated


async def record_send(repos: Repos, *, contact: Dict[str, Any], to: str, kind: str, body: str,
                      result: wa.SendResult, template: Optional[str] = None, sent_by: Optional[str] = None) -> Dict[str, Any]:
    doc = {"_id": new_id("wa"), "contact_id": contact.get("_id"), "to": to, "kind": kind, "template": template,
           "body": body[:4000], "message_id": result.message_id, "sent_by": sent_by,
           "status": "accepted" if result.ok else "failed",
           "error_code": result.error_code, "error": None if result.ok else result.error}
    await repos.wa_outbox.insert_one(doc)
    return doc


def fill_template(body: str, params: List[str]) -> str:
    """Preview of what the patient receives: {{1}}… replaced by the given values."""
    def sub(m: Any) -> str:
        i = int(m.group(1))
        return params[i - 1] if 0 < i <= len(params) else m.group(0)

    return wa.PLACEHOLDER.sub(sub, body or "")
