"""
Sales reports and the daily digest (SALES_SUITE_PLAN.md Phase 18), from the built-in CRM.

    pipeline   open deal value by stage, and the weighted forecast (amount × probability)
    results    win rate (won / closed), average won deal, days from open to won
    meetings   booked meetings by offer, campaign and lead source
    activity   what each person did each week (calls, notes, emails, stage moves)
    funnel     leads → called → reached → meeting → won

The digest emails each sales-team member their tasks due today and their open deals, once a day at
SALES_DIGEST_HOUR_UTC.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from config import settings
from core.outbound import crm, followup
from core.outbound import models as m
from core.outbound.offers import OFFERS

logger = logging.getLogger(__name__)

DIGEST_META = "sales_digest_meta"


def _aware(v: Any) -> Optional[datetime]:
    if not isinstance(v, datetime):
        return None
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


async def _distinct_leads(db: Any, match: Dict[str, Any]) -> int:
    """How many different leads have an attempt matching `match` (counted in Mongo)."""
    rows = await db[m.ATTEMPTS].aggregate([{"$match": match}, {"$group": {"_id": "$lead_id"}},
                                           {"$count": "n"}]).to_list(1)
    return int(rows[0]["n"]) if rows else 0


async def report(db: Any, *, days: int = 90, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    by_stage = await crm.stage_totals(db)
    # Closed in the window: by closed_at, or (older deals without it) when they were last changed.
    closed = await db[crm.DEALS].find(
        {"stage": {"$in": ["won", "lost"]},
         "$or": [{"closed_at": {"$gte": since}}, {"closed_at": None, "updated_at": {"$gte": since}}]},
        {"stage": 1, "amount": 1, "won_at": 1, "created_at": 1, "lead_id": 1}).to_list(20_000)
    won = [d for d in closed if d["stage"] == "won"]
    cycle = [((_aware(d.get("won_at")) or now) - (_aware(d.get("created_at")) or now)).days for d in won]
    done = {"created_at": {"$gte": since}, "status": "done"}
    meetings = await db[m.ATTEMPTS].find({**done, "outcome": "demo_booked"},
                                         {"offer_id": 1, "campaign_id": 1, "lead_id": 1, "kind": 1}).to_list(20_000)
    called = await _distinct_leads(db, done)
    reached = await _distinct_leads(db, {**done, "$or": [{"amd": "human"}, {"outcome": {"$in": list(m.HUMAN_OUTCOMES)}}]})
    lead_source = {}
    if meetings:
        for lead in await db[m.LEADS].find({"_id": {"$in": [a["lead_id"] for a in meetings]}}, {"source": 1}).to_list(10_000):
            lead_source[lead["_id"]] = lead.get("source") or "—"
    names = {c["_id"]: c.get("name") for c in await db[m.CAMPAIGNS].find({}, {"name": 1}).to_list(1000)}
    offers = {o["_id"]: o.get("name") for o in await db[OFFERS].find({}, {"name": 1}).to_list(1000)}
    activity: Dict[str, Counter] = defaultdict(Counter)
    async for a in db[followup.ACTIVITY].aggregate([
            {"$match": {"at": {"$gte": now - timedelta(weeks=4)}}},
            {"$group": {"_id": {"by": "$by", "type": "$type"}, "n": {"$sum": 1}}}]):
        activity[str(a["_id"].get("by") or "system")][a["_id"].get("type") or "note"] += int(a["n"])
    leads_total = await db[m.LEADS].count_documents({"owner": m.OWNER_PLATFORM, "status": {"$ne": "merged"}})
    return {
        "days": days,
        "pipeline": {"stages": by_stage, "open_value": round(sum(by_stage[s]["value"] for s in crm.OPEN_STAGES), 2),
                     "forecast": round(sum(by_stage[s]["weighted"] for s in crm.OPEN_STAGES), 2)},
        "results": {"won": len(won), "lost": len(closed) - len(won),
                    "win_rate": round(len(won) / len(closed), 4) if closed else None,
                    "average_won": round(sum(float(d.get("amount") or 0) for d in won) / len(won), 2) if won else None,
                    "cycle_days": round(sum(cycle) / len(cycle), 1) if cycle else None},
        "meetings": {"total": len(meetings),
                     "by_offer": dict(Counter(offers.get(a.get("offer_id") or "off_laudline", a.get("offer_id") or "LaudLine (built-in)") for a in meetings)),
                     "by_campaign": dict(Counter("Single calls" if a.get("kind") == "single" else names.get(a.get("campaign_id"), "—")
                                                 for a in meetings)),
                     "by_source": dict(Counter(lead_source.get(a["lead_id"], "—") for a in meetings))},
        "activity": {who: dict(c) for who, c in activity.items()},
        "funnel": {"leads": leads_total, "called": called, "reached": reached,
                   "meetings": len({a["lead_id"] for a in meetings}), "won": len({d["lead_id"] for d in won})},
    }


async def send_digests(db: Any, *, now: Optional[datetime] = None) -> Dict[str, int]:
    """Once a day at SALES_DIGEST_HOUR_UTC: each team member's tasks and open deals, by email."""
    from core.integrations.email import send_email

    now = now or datetime.now(timezone.utc)
    stats = {"sent": 0}
    if now.hour != settings.SALES_DIGEST_HOUR_UTC:
        return stats
    day = now.strftime("%Y-%m-%d")
    people = await db.users.find({"sales_role": {"$in": ["caller", "manager"]}, "disabled": {"$ne": True}},
                                 {"email": 1, "name": 1}).to_list(500)
    for p in people:
        work = await crm.my_work(db, p["_id"], now=now)
        if not work["tasks"] and not work["deals"]:
            continue
        lines = [f"Good morning {p.get('name') or ''}".strip() + ",", "",
                 f"{len(work['tasks'])} task{'s' if len(work['tasks']) != 1 else ''} due ({work['overdue']} overdue):"]
        lines += [f"- {t.get('title')}" for t in work["tasks"][:20]]
        lines += ["", f"{len(work['deals'])} open deal{'s' if len(work['deals']) != 1 else ''}, ${work['pipeline_value']:,.0f} weighted:"]
        lines += [f"- {d.get('title')} ({d.get('stage', '').replace('_', ' ')})" for d in work["deals"][:20]]
        # Claimed per person: one failed send is tried again on the next tick (within the hour), and
        # nobody gets it twice.
        key = f"{day}:{p['_id']}"
        if await db[DIGEST_META].find_one_and_update({"_id": key}, {"$setOnInsert": {"at": now}}, upsert=True):
            continue
        try:
            sent = bool(await send_email(p["email"], "Your sales day", "\n".join(lines)))
        except Exception:
            logger.warning("Digest email failed", exc_info=True)
            sent = False
        if sent:
            stats["sent"] += 1
        else:
            await db[DIGEST_META].delete_one({"_id": key})
    return stats
