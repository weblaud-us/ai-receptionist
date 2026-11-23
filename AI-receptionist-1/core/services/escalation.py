"""
On-call escalation for urgent dental calls (plan Phase 2.3).

When `triage_dental_emergency` saves an urgent or life-threatening lead and the practice has an
on-call list (`tenant.escalation`), the first person is alerted by WhatsApp and e-mail with a link
to acknowledge. If nobody acknowledges within `ack_minutes`, the next person is alerted, then the
owner. It stops as soon as anyone acknowledges (from the link or the dashboard).

The tenant jobs tick (services/tenant_jobs.py) moves escalations along; each step is claimed with
an atomic update so two workers never alert twice. No SMS: WhatsApp free-form messages only reach
someone who messaged the practice's WhatsApp number in the last 24 hours, so e-mail is always sent too.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from config import settings
from core.integrations import email as email_api
from core.integrations import whatsapp as wa_api
from core.repositories import Repos
from core.sensitive import seal, unseal
from core.signed_links import sign_ref, verify_ref
from core.tenancy.ids import new_id
from core.tenancy.models import OnCallContact, TenantConfig
from database.mongodb import get_db

logger = logging.getLogger(__name__)

OPEN = "open"
ACKED = "acknowledged"
EXHAUSTED = "unacknowledged"          # everyone (and the owner) alerted, nobody acknowledged


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def ack_url(tenant_id: str, escalation_id: str) -> str:
    """One-tap acknowledge link (a page with a button: link scanners can't acknowledge by fetching it)."""
    base = (settings.WEBHOOK_BASE_URL or "").rstrip("/")
    if not base:
        return ""
    ref = f"{tenant_id}:{escalation_id}"
    return f"{base}/public/escalations/{tenant_id}/{escalation_id}?sig={sign_ref('escalation', ref)}"


def verify_ack(tenant_id: str, escalation_id: str, sig: Optional[str]) -> bool:
    return verify_ref("escalation", f"{tenant_id}:{escalation_id}", sig)


SEALED = ("details",)                 # symptoms are health data: encrypted at rest, like lead problems


def _message(t: TenantConfig, esc: Dict[str, Any], *, final: bool = False) -> str:
    esc = unseal(esc, SEALED)
    who = "Nobody on call has acknowledged this yet. " if final else ""
    link = ack_url(t.id, esc["_id"])
    return (f"{who}{esc['headline']}\n{esc['details']}\n\n"
            + (f"Tap to say you're handling it: {link}" if link else "Acknowledge it in the dashboard (Leads)."))


async def _alert(t: TenantConfig, person: OnCallContact, text: str, subject: str) -> Dict[str, bool]:
    out: Dict[str, bool] = {}
    if person.whatsapp:
        out["whatsapp"] = await wa_api.send_text(person.whatsapp, f"{t.name}: {text}",
                                                 t.integrations.whatsapp.phone_number_id or None)
    if person.email:
        out["email"] = await email_api.send_email(person.email, f"URGENT: {subject}", text)
    return out


async def start(t: TenantConfig, *, lead_id: str, call_id: Optional[str], severity: str,
                headline: str, details: str, now: Optional[datetime] = None) -> Optional[str]:
    """Open an escalation and alert the first on-call person. None when the practice has no list."""
    cfg = t.escalation
    if not cfg.active() or severity not in ("urgent", "life_threatening"):
        return None
    now = _aware(now or utcnow())
    repos = Repos(get_db(), t.id)
    esc: Dict[str, Any] = {"_id": new_id("esc"), "lead_id": lead_id, "call_id": call_id, "severity": severity,
           "headline": headline, "details": details, "status": OPEN, "step": 0, "created_at": now,
           "next_at": now + timedelta(minutes=cfg.ack_minutes), "history": []}
    await repos.escalations.insert_one(seal(esc, SEALED))
    await repos.leads.update_one({"_id": lead_id}, {"$set": {"escalation_status": OPEN, "escalation_id": esc["_id"]}})
    await _alert_step(t, repos, esc, 0, now)
    return str(esc["_id"])


async def _alert_step(t: TenantConfig, repos: Repos, esc: Dict[str, Any], step: int, now: datetime) -> None:
    people = [c for c in t.escalation.on_call if c.whatsapp or c.email]
    if step < len(people):
        person = people[step]
        sent = await _alert(t, person, _message(t, esc), esc["headline"])
        entry = {"step": step, "at": now, "to": person.name or f"on-call #{step + 1}", "sent": sent}
    else:                              # everyone on the list tried: the owner, over every channel
        from core.notifications import notify_owner

        sent = await notify_owner(t, "on_message", _message(t, esc, final=True), subject=esc["headline"], urgent=True)
        entry = {"step": step, "at": now, "to": "owner", "sent": sent}
    await repos.escalations.update_one({"_id": esc["_id"]}, {"$push": {"history": entry}})
    if not any(sent.values()):
        logger.error("Escalation %s step %d reached nobody for %s", esc["_id"], step, t.id)


async def acknowledge(t: TenantConfig, escalation_id: str, by: str) -> bool:
    repos = Repos(get_db(), t.id)
    now = utcnow()
    res = await repos.escalations.update_one({"_id": escalation_id, "status": {"$in": [OPEN, EXHAUSTED]}},
                                             {"$set": {"status": ACKED, "acked_by": by[:80], "acked_at": now}})
    if not res.modified_count:
        return False
    esc = await repos.escalations.find_one({"_id": escalation_id})
    if esc:
        await repos.leads.update_one({"_id": esc["lead_id"]}, {"$set": {"escalation_status": ACKED,
                                                                       "escalation_acked_by": by[:80]}})
    logger.info("Escalation %s acknowledged by %s", escalation_id, by[:80])
    return True


async def run_due(t: TenantConfig, now: datetime) -> int:
    """Alert the next person for every open escalation nobody acknowledged in time."""
    now = _aware(now)
    repos = Repos(get_db(), t.id)
    due = await repos.escalations.find({"status": OPEN, "next_at": {"$lte": now}}, limit=20)
    people = len([c for c in t.escalation.on_call if c.whatsapp or c.email])
    moved = 0
    for esc in due:
        step = int(esc["step"]) + 1
        last = step >= people                       # this step goes to the owner
        claimed = await repos.escalations.update_one(
            {"_id": esc["_id"], "status": OPEN, "step": esc["step"]},
            {"$set": {"step": step, "status": EXHAUSTED if last else OPEN,
                      "next_at": now + timedelta(minutes=t.escalation.ack_minutes)}})
        if not claimed.modified_count:
            continue
        if last:
            await repos.leads.update_one({"_id": esc["lead_id"]}, {"$set": {"escalation_status": EXHAUSTED}})
        await _alert_step(t, repos, esc, step if not last else people, now)
        moved += 1
    return moved
