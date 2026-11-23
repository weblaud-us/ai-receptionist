"""
The front-desk inbox (plan 4.1): everything a person has to follow up, in one list.

It is a view, not a copy: each item is the original record (a message, a lead, an escalation, an
appointment whose confirmation call didn't get an answer), so nothing can drift out of step.
Assigning and "done" write to that record:

    message        done = handled
    lead           done = contacted
    escalation     done = acknowledged (the on-call list stops being paged)
    confirmation   done = followed up (appointment.confirmation.followed_up_at)

Every item has a due time (when it should be dealt with by) and is overdue after it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from core.repositories import Repos
from core.sensitive import unseal
from core.tenancy.models import TenantConfig

KINDS = ("message", "lead", "escalation", "confirmation")
FOLLOW_UP = ("no_answer", "voicemail", "no_decision", "failed")


def _aware(dt: Any) -> Optional[datetime]:
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _item(kind: str, rid: str, *, title: str, detail: str, created: Any, due: Optional[datetime],
          assignee: Optional[str], urgent: bool, phone: str = "", link: str = "") -> Dict[str, Any]:
    return {"kind": kind, "id": rid, "title": title, "detail": detail[:300], "created_at": _aware(created),
            "due_at": due, "assignee_id": assignee, "urgent": urgent, "phone": phone, "link": link}


async def items(repos: Repos, t: TenantConfig, now: datetime) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in await repos.messages.find({"status": "new"}, sort=[("created_at", -1)], limit=200):
        created = _aware(m.get("created_at")) or now
        urgent = bool(m.get("urgent"))
        out.append(_item("message", m["message_id"], title=f"Call back {m.get('caller_name') or 'caller'}",
                         detail=str(m.get("message") or ""), created=created,
                         due=created + timedelta(hours=1 if urgent else 24), assignee=m.get("assignee_id"),
                         urgent=urgent, phone=str(m.get("callback_number") or ""), link="/calls"))
    for raw in await repos.leads.find({"status": "new"}, sort=[("created_at", -1)], limit=200):
        lead = unseal(raw)
        created = _aware(lead.get("created_at")) or now
        emergency = lead.get("kind") == "dental_emergency"
        urgent = emergency and lead.get("severity") in ("urgent", "life_threatening")
        label = "Urgent dental problem" if urgent else "Dental problem" if emergency else "Treatment enquiry" \
            if lead.get("kind") == "cosmetic_consult" else "Job request"
        out.append(_item("lead", lead["_id"], title=f"{label}: {lead.get('name') or 'caller'}",
                         detail=str(lead.get("problem") or lead.get("treatment_category") or ""), created=created,
                         due=created + timedelta(hours=1 if urgent else 24), assignee=lead.get("assignee_id"),
                         urgent=urgent, phone=str(lead.get("phone") or ""), link="/leads"))
    for e in await repos.escalations.find({"status": {"$in": ["open", "unacknowledged"]}},
                                          projection={"details_enc": 0}, sort=[("created_at", -1)], limit=50):
        out.append(_item("escalation", e["_id"], title=f"Nobody has taken it yet: {e.get('headline') or 'urgent call'}",
                         detail="The on-call list is being alerted.", created=e.get("created_at"), due=now,
                         assignee=e.get("assignee_id"), urgent=True, link="/leads"))
    tz_now = now
    for a in await repos.appointments.find(
            {"status": "confirmed", "start": {"$gte": tz_now}, "confirmation.status": {"$in": list(FOLLOW_UP)},
             "confirmation.followed_up_at": None}, sort=[("start", 1)], limit=100):
        start = _aware(a.get("start")) or now
        status = (a.get("confirmation") or {}).get("status")
        out.append(_item("confirmation", a["appointment_id"],
                         title=f"Confirm {a.get('name') or 'patient'}'s appointment",
                         detail=f"Confirmation call: {str(status).replace('_', ' ')}.", created=start,
                         due=start - timedelta(hours=2), assignee=(a.get("confirmation") or {}).get("assignee_id"),
                         urgent=False, phone=str(a.get("phone") or ""), link="/appointments"))
    for i in out:
        i["overdue"] = bool(i["due_at"] and i["due_at"] < now)
    out.sort(key=lambda i: (not i["overdue"], not i["urgent"], i["due_at"] or now))
    return out


async def update(repos: Repos, kind: str, rid: str, *, assignee_id: Any = ..., done: bool = False,
                 by: str = "", now: datetime) -> bool:
    """Assign (None = unassign) and/or mark done. False when there is no such open item."""
    sets: Dict[str, Any] = {}
    if kind == "message":
        if assignee_id is not ...:
            sets["assignee_id"] = assignee_id
        if done:
            sets.update(status="handled", handled_by=by, handled_at=now)
        res = await repos.messages.update_one({"message_id": rid}, {"$set": {**sets, "updated_at": now}})
    elif kind == "lead":
        if assignee_id is not ...:
            sets["assignee_id"] = assignee_id
        if done:
            sets["status"] = "contacted"
        res = await repos.leads.update_one({"_id": rid}, {"$set": {**sets, "updated_at": now}})
    elif kind == "confirmation":
        if assignee_id is not ...:
            sets["confirmation.assignee_id"] = assignee_id
        if done:
            sets.update({"confirmation.followed_up_at": now, "confirmation.followed_up_by": by})
        if not sets:
            return bool(await repos.appointments.find_one({"appointment_id": rid}, {"_id": 1}))
        res = await repos.appointments.update_one({"appointment_id": rid}, {"$set": sets})
    elif kind == "escalation":
        if assignee_id is not ...:
            await repos.escalations.update_one({"_id": rid}, {"$set": {"assignee_id": assignee_id}})
        if done:
            from core.services import escalation
            from core.tenancy import resolve_by_id

            tenant = await resolve_by_id(repos.tenant_id)
            return bool(tenant) and await escalation.acknowledge(tenant, rid, by=by or "front desk")
        return bool(await repos.escalations.find_one({"_id": rid}, {"_id": 1}))
    else:
        return False
    return bool(getattr(res, "matched_count", 0))
