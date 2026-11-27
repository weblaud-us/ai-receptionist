"""
What the agent remembers about a business from earlier calls (SALES_LEARNING_PLAN.md Phase 1).

Built in code from the lead's own finished attempts — no model call, nothing made up:

- the last MAX_CONVERSATIONS conversations: when, who answered (role, the decision-maker or a
  gatekeeper, names), their problems, what they use today, their objections, the outcome, the next
  step that was agreed, what we sent, and a person's wrap-up notes;
- how many other tries nobody answered.

Only calls made for the same company count: a prospect who spoke to "WebLaud" must not hear the
LaudLine agent claim that conversation. "Forget" (`memory_cleared_at`) starts the memory again.

Everything here came from a phone call, so it is treated like any other untrusted text: fence tags
are stripped, and an item that offers a discount, a free period, a promise, a link or reads like an
instruction to the agent is dropped rather than passed on. The prompt says it may be wrong.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from config import settings
from core.outbound import models as m
from core.outbound import offers as of

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 10
MAX_CONVERSATIONS = 3
MAX_CHARS = 700
ITEM_CHARS = 120
_NO_ANSWER = frozenset({"no_answer", "busy", "failed", "voicemail", "machine", "uncertain"})
# Offers we never make on a call, and text that tries to steer the agent: such an item is dropped.
# ("free after 2pm" or "their booking system" are fine; "a free month" or "ignore your rules" are not.)
_RISKY = re.compile(
    r"\b(discount\w*|promis\w*|guarantee\w*|refund\w*|percent|per cent|for free|"
    r"free (?:trial|month|months|week|weeks|period|setup|set-up|of charge|upgrade|plan|version)|"
    r"ignore (?:all|any|previous|prior|the|your|these)|(?:system|developer) (?:prompt|note|message|instruction\w*)|"
    r"new instructions?|you are now|pretend (?:to be|you)|act as)\b|%|https?://|www\.|[<>{}]", re.IGNORECASE)
_PROJECTION = {"outcome": 1, "amd": 1, "status": 1, "created_at": 1, "ended_at": 1, "qualification": 1,
               "next_step": 1, "summary": 1, "events": 1, "email_consent_at": 1, "wrapup": 1, "operator": 1,
               "mode": 1, "taken_over": 1, "offer_id": 1, "offer_version": 1}


def _aware(dt: Any) -> Optional[datetime]:
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def clean_item(text: Any, limit: int = ITEM_CHARS) -> str:
    """A short, safe piece of what was said, or "" when it shouldn't reach the agent at all."""
    t = of.FENCE_TAG.sub(" ", str(text or ""))
    t = " ".join(t.split())
    if not t or _RISKY.search(t):
        return ""
    return t[:limit].rstrip(" ,;:-")


def _talked(a: Mapping[str, Any]) -> bool:
    outcome = a.get("outcome")
    if a.get("status") != "done" or outcome in m.UNCOUNTED_OUTCOMES or outcome == "wrong_number":
        return False
    return a.get("amd") == "human" or outcome in m.HUMAN_OUTCOMES


def _when(at: Optional[datetime], zone: str, now: datetime) -> str:
    if at is None:
        return "Earlier"
    try:
        local = at.astimezone(ZoneInfo(zone)) if zone else at
    except Exception:
        local = at
    days = (now.date() - at.date()).days
    ago = "today" if days <= 0 else "yesterday" if days == 1 else f"{days} days ago"
    return f"{local:%a} {local.day} {local:%b} ({ago})"


def _who(a: Mapping[str, Any]) -> str:
    q = a.get("qualification") or {}
    people = []
    for p in (q.get("people") or [])[:3]:
        if isinstance(p, Mapping):
            name, role = clean_item(p.get("name"), 40), clean_item(p.get("role"), 40)
            if name or role:
                people.append(f"{name} ({role})" if name and role else name or role)
    role = clean_item(q.get("role"), 60)
    bits = [f"spoke to {', '.join(people)}" if people else (f"spoke to the {role}" if role else "spoke to someone")]
    if q.get("gatekeeper") is True:
        bits.append("a gatekeeper")
    if q.get("decision_maker") is True:
        bits.append("the person who decides")
    elif q.get("decision_maker") is False and q.get("gatekeeper") is not True:
        bits.append("not the person who decides")
    if a.get("operator") or a.get("mode") == "human_assisted" or a.get("taken_over"):
        bits.append("a person from our team took the call")
    return ", ".join(bits)


def _what(a: Mapping[str, Any]) -> List[str]:
    q = a.get("qualification") or {}
    out = []
    pains = [x for x in (clean_item(p, 60) for p in (q.get("pains") or [])[:2]) if x]
    if pains:
        out.append("problems: " + "; ".join(pains))
    current = clean_item(q.get("current_solution"), 60)
    if current:
        out.append(f"uses today: {current}")
    objections = [x for x in (clean_item(o, 60) for o in (q.get("objections") or [])[:2]) if x]
    if objections:
        out.append("objections: " + "; ".join(objections))
    best = clean_item(q.get("best_time_to_reach"), 60)
    if best:
        out.append(f"best time to reach them: {best}")
    return out


def _result(a: Mapping[str, Any]) -> List[str]:
    out = [f"outcome: {m.OUTCOME_LABELS.get(str(a.get('outcome')), str(a.get('outcome') or '').replace('_', ' '))}"]
    wrapup = a.get("wrapup") or {}
    step = clean_item(wrapup.get("next_step") or a.get("next_step"), 160)
    if step:
        out.append(f"next step: {step}")
    events = set(a.get("events") or [])
    if "info_email" in events or (not a.get("events") and a.get("email_consent_at") and a.get("outcome") == "interested"):
        out.append("we emailed them the details")
    if "info_whatsapp" in events:
        out.append("we sent the details on WhatsApp")
    notes = clean_item(wrapup.get("notes"), 200)
    if notes:
        out.append(f"our notes: {notes}")
    return out


def conversation_line(a: Mapping[str, Any], *, zone: str, now: datetime) -> str:
    parts = [_who(a), *_what(a), *_result(a)]
    return f"{_when(_aware(a.get('created_at')), zone, now)}: " + "; ".join(p for p in parts if p) + "."


def brief_lines(lead: Mapping[str, Any], attempts: Sequence[Mapping[str, Any]], *, company: str,
                company_of: Callable[[Mapping[str, Any]], str], now: datetime, zone: str,
                max_chars: int = MAX_CHARS) -> List[str]:
    """The <earlier_calls> lines, newest first, within `max_chars`. Pure: `attempts` are this lead's
    finished calls, newest first."""
    same = [a for a in attempts if (company_of(a) or "").lower() == company.lower()]
    talked = [a for a in same if _talked(a)][:MAX_CONVERSATIONS]
    unanswered = [a for a in same if a.get("outcome") in _NO_ANSWER and not _talked(a)]
    lines: List[str] = []
    used = 0
    for a in talked:
        line = conversation_line(a, zone=zone, now=now)
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line)
    if unanswered:
        last = _when(_aware(unanswered[0].get("created_at")), zone, now)
        line = (f"{len(unanswered)} other {'try was' if len(unanswered) == 1 else 'tries were'} not answered "
                f"(last: {last}).")
        if used + len(line) <= max_chars:
            lines.append(line)
    return lines


def lead_zone(lead: Mapping[str, Any]) -> str:
    zones = [z for z in (lead.get("timezones") or []) if z]
    return str(lead.get("timezone") or (zones[0] if zones else "") or "UTC")


def window_start(lead: Mapping[str, Any], now: datetime) -> datetime:
    since = now - timedelta(days=max(1, int(settings.SALES_MEMORY_DAYS)))
    cleared = _aware(lead.get("memory_cleared_at"))
    return max(since, cleared) if cleared else since


async def lead_brief(db: Any, lead: Mapping[str, Any], offer: Mapping[str, Any], *, now: Optional[datetime] = None,
                     exclude_attempt_id: Optional[str] = None) -> List[str]:
    """What the agent may know about earlier calls with this business. [] when memory is off or
    there is nothing to say. Never raises: a dial never waits on, or fails because of, memory."""
    if not settings.SALES_LEAD_MEMORY_ENABLED:
        return []
    now = now or datetime.now(timezone.utc)
    try:
        query: Dict[str, Any] = {"lead_id": lead["_id"], "status": "done", "created_at": {"$gte": window_start(lead, now)}}
        if exclude_attempt_id:
            query["_id"] = {"$ne": exclude_attempt_id}
        rows = await db[m.ATTEMPTS].find(query, _PROJECTION).sort("created_at", -1).limit(MAX_ATTEMPTS).to_list(MAX_ATTEMPTS)
        if not rows:
            return []
        companies: Dict[tuple, str] = {}
        for r in rows:
            key = (r.get("offer_id"), r.get("offer_version"))
            if key not in companies:
                companies[key] = str((await of.offer_for(db, key[0], key[1])).get("company_name") or "")
        return brief_lines(lead, rows, company=str(offer.get("company_name") or ""),
                           company_of=lambda r: companies.get((r.get("offer_id"), r.get("offer_version")), ""),
                           now=now, zone=lead_zone(lead))
    except Exception:
        logger.warning("Lead memory skipped for %s", lead.get("_id"), exc_info=True)
        return []


def this_call_line(lead: Mapping[str, Any], *, consented: bool) -> str:
    """How this call came about, for "# This call"."""
    if lead.get("claimed_from") == "callback":
        note = clean_item(lead.get("callback_note"), 160)
        return ("They asked us to call back about now" + (f" ({note})" if note else "") + ". Be brief and get "
                "straight to what you agreed to talk about.")
    if consented:
        return "They filled in a form asking for this call."
    return "They are not expecting your call. Be brief and respectful of their time."
