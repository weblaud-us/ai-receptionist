"""
Auto-pause guardrails (SALES_SUITE_PLAN.md Phase 11): a running campaign stops itself when the calls
show something is wrong, and the team gets one email.

    opt-outs       more than max_optout_rate of the people reached asked not to be called
    answer rate    fewer than min_answer_rate of dials connect (often: the caller ID is flagged as spam)
    errors         more than max_error_rate of dials fail
    cost           cost per meeting goes over the limit (when one is set)

Each rule waits for a minimum sample, so two bad calls never pause a campaign. Restarting a paused
campaign runs the launch checklist again. Thresholds live on the campaign (`guardrails`), are not a
material field, and come from these rules, never from a model.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

from config import settings
from core.outbound import models as m

logger = logging.getLogger(__name__)

CHECK_EVERY = timedelta(seconds=60)
_CONNECTED = m.HUMAN_OUTCOMES | {"voicemail", "machine", "uncertain"}


@dataclass
class Trip:
    code: str
    detail: str
    metrics: Dict[str, Any]


def metrics(attempts: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    rows = [a for a in attempts if a.get("status") == "done" and a.get("outcome") not in m.UNCOUNTED_OUTCOMES]
    humans = [a for a in rows if a.get("amd") == "human" or a.get("outcome") in m.HUMAN_OUTCOMES]
    return {
        "dials": len(rows),
        "connected": sum(1 for a in rows if a.get("amd") or a.get("outcome") in _CONNECTED),
        "humans": len(humans),
        "opt_outs": sum(1 for a in rows if a.get("outcome") == "dnc"),
        "errors": sum(1 for a in rows if a.get("outcome") == "failed"),
        "meetings": sum(1 for a in rows if a.get("outcome") == "demo_booked"),
        "cost_usd": round(sum(float(a.get("cost_usd") or 0) for a in rows), 2),
    }


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def evaluate(guardrails: Mapping[str, Any], attempts: Iterable[Mapping[str, Any]]) -> List[Trip]:
    """Which guardrails the recent attempts break. Pure: no clock, no database."""
    g = m.Guardrails.model_validate(dict(guardrails or {}))
    if not g.enabled:
        return []
    x = metrics(attempts)
    trips: List[Trip] = []
    if x["humans"] >= g.min_humans_for_optout and x["opt_outs"] / x["humans"] > g.max_optout_rate:
        rate = x["opt_outs"] / x["humans"]
        trips.append(Trip("opt_outs", f"{x['opt_outs']} of {x['humans']} people reached ({_pct(rate)}) asked not to be "
                                      f"called again; the limit is {_pct(g.max_optout_rate)}. Check the list and the opener.", x))
    if x["dials"] >= g.min_dials_for_answer and x["connected"] / x["dials"] < g.min_answer_rate:
        rate = x["connected"] / x["dials"]
        trips.append(Trip("answer_rate", f"Only {x['connected']} of {x['dials']} calls connected ({_pct(rate)}); the "
                                         f"floor is {_pct(g.min_answer_rate)}. The caller ID may be flagged as spam.", x))
    if x["dials"] >= g.min_dials_for_error and x["errors"] / x["dials"] > g.max_error_rate:
        rate = x["errors"] / x["dials"]
        trips.append(Trip("errors", f"{x['errors']} of {x['dials']} calls failed ({_pct(rate)}); the limit is "
                                    f"{_pct(g.max_error_rate)}. Check the trunk and caller IDs.", x))
    if g.max_cost_per_meeting_usd:
        per = x["cost_usd"] / x["meetings"] if x["meetings"] else x["cost_usd"]
        if per > g.max_cost_per_meeting_usd:
            trips.append(Trip("cost", f"${x['cost_usd']:.2f} spent for {x['meetings']} meeting"
                                      f"{'' if x['meetings'] == 1 else 's'}; the limit is ${g.max_cost_per_meeting_usd:.2f} "
                                      "a meeting.", x))
    return trips


async def alert(subject: str, text: str) -> int:
    """Email everyone in SALES_ALERT_EMAILS. Best effort; returns how many were sent."""
    from core.integrations.email import send_email

    sent = 0
    for to in settings.sales_alert_emails:
        try:
            sent += int(bool(await send_email(to, subject, text)))
        except Exception:
            logger.warning("Sales alert email failed", exc_info=True)
    return sent


async def _system_audit(db: Any, action: str, target_type: str, target_id: str, diff: Dict[str, Any], *,
                        actor: str = "system:guardrails") -> None:
    """An audit entry for something the system did on its own (guardrails, the learning loop)."""
    from core.repositories import Repos
    from utils.serialize import to_jsonable

    try:
        await Repos(db, "platform").audit.record(user_id=actor, action=action, target_type=target_type,
                                                 target_id=target_id, diff=to_jsonable(diff))
    except Exception:
        logger.warning("Could not audit %s", action, exc_info=True)


async def check_all(db: Any, *, now: Optional[datetime] = None) -> Dict[str, int]:
    """Pause every running campaign that breaks a guardrail (each campaign looked at once a minute)."""
    now = now or datetime.now(timezone.utc)
    stats = {"checked": 0, "paused": 0}
    campaigns = await db[m.CAMPAIGNS].find({"status": "running", "$or": [
        {"guardrails_checked_at": None}, {"guardrails_checked_at": {"$exists": False}},
        {"guardrails_checked_at": {"$lt": now - CHECK_EVERY}}]}).to_list(200)
    for c in campaigns:
        stats["checked"] += 1
        g = m.Guardrails.model_validate(c.get("guardrails") or {})
        rows = await db[m.ATTEMPTS].find(
            {"campaign_id": c["_id"], "created_at": {"$gte": now - timedelta(hours=g.window_hours)}},
            {"status": 1, "outcome": 1, "amd": 1, "cost_usd": 1}).to_list(20_000)
        trips = evaluate(g.model_dump(), rows)
        if not trips:
            await db[m.CAMPAIGNS].update_one({"_id": c["_id"]}, {"$set": {"guardrails_checked_at": now}})
            continue
        reason = {"code": trips[0].code, "detail": trips[0].detail, "trips": [asdict(t) for t in trips], "at": now}
        res = await db[m.CAMPAIGNS].update_one({"_id": c["_id"], "status": "running"}, {"$set": {
            "status": "paused", "paused_reason": reason, "guardrails_checked_at": now, "updated_at": now}})
        if not res.modified_count:
            continue
        stats["paused"] += 1
        await _system_audit(db, "sales.campaign.auto_pause", "campaign", c["_id"], {"code": trips[0].code, "trips": reason["trips"]})
        await alert(f"Sales campaign paused: {c.get('name')}",
                    f"The campaign \"{c.get('name')}\" paused itself.\n\n" + "\n".join(f"- {t.detail}" for t in trips)
                    + "\n\nCalls in progress finish. Fix the cause, then start it again from Sales dialer → Campaigns; "
                      "the launch checklist runs again.")
    return stats
