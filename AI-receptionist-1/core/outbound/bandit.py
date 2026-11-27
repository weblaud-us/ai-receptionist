"""
Calls shift toward the winning script (SALES_LEARNING_PLAN.md Phase 7).

A campaign A/B tests up to two approved scripts (both covered by its legal review, so moving traffic
between them is not a material change). With `script_split: "auto"`:

- the split stays even until each script has reached WARMUP businesses;
- then each script gets the chance that it is the better one at producing a real next step
  (learning_stats), clipped to [FLOOR, 1 − FLOOR] so the other keeps being measured;
- a script that makes more people hang up during the opener, or opt out, never gains traffic;
- the split is worked out at most once a day (and stored), so it moves slowly;
- each business keeps the script it got first in a campaign (`lead.script_pick`), and every attempt
  records how its script was chosen and with what probability (`variant_pick`), so results can be
  re-analysed fairly.

The split is independent of the playbook test (core/outbound/experiments.assign_arm hashes the lead
with the test's own salt). Human-assisted and legacy campaigns keep the even hash split.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from core.outbound import learning_stats as ls
from core.outbound import models as m
from core.outbound import offers as of
from core.outbound.experiments import prob_better

logger = logging.getLogger(__name__)

STATS = "sales_learning_stats"
WARMUP = 30
FLOOR = 0.2
GUARD_P = 0.9
REFRESH_EVERY = timedelta(hours=24)
WINDOW = timedelta(days=30)


def _aware(dt: Any) -> Optional[datetime]:
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def p_best(stats: Mapping[str, ls.ArmStats], variant_ids: Sequence[str]) -> Dict[str, float]:
    """The chance each script is the better one (two scripts: exact; more: seeded draws)."""
    ids = list(variant_ids)
    s = {v: stats.get(v, ls.ArmStats()) for v in ids}
    if len(ids) == 2:
        a, b = ids
        pb = prob_better(s[a].progress, s[a].decided, s[b].progress, s[b].decided)
        return {a: 1 - pb, b: pb}
    rng = random.Random(1)
    wins = {v: 0 for v in ids}
    for _ in range(4000):
        draws = {v: rng.betavariate(1 + s[v].progress, 1 + max(0, s[v].decided - s[v].progress)) for v in ids}
        wins[max(draws, key=lambda k: draws[k])] += 1
    return {v: wins[v] / 4000 for v in ids}


def allocation(stats: Mapping[str, ls.ArmStats], variant_ids: Sequence[str]) -> Tuple[Dict[str, float], str]:
    """(share of new businesses per script, how it was decided: "even" | "auto")."""
    ids = list(variant_ids)
    if len(ids) < 2:
        return ({ids[0]: 1.0} if ids else {}), "even"
    even = {v: 1 / len(ids) for v in ids}
    if any(stats.get(v, ls.ArmStats()).leads < WARMUP for v in ids):
        return even, "even"
    best = p_best(stats, ids)
    alloc = {v: min(1 - FLOOR, max(FLOOR, best[v])) for v in ids}
    capped = set()
    for v in ids:                                   # never more traffic for a script that drives people off
        sv = stats[v]
        for o in ids:
            if o == v:
                continue
            so = stats[o]
            worse = (prob_better(so.opener_hangups, so.leads, sv.opener_hangups, sv.leads) >= GUARD_P
                     or prob_better(so.opt_outs, so.leads, sv.opt_outs, sv.leads) >= GUARD_P)
            if worse and alloc[v] > even[v]:
                alloc[v] = even[v]
                capped.add(v)
    free = [v for v in ids if v not in capped]
    left = 1 - sum(alloc[v] for v in capped)
    free_total = sum(alloc[v] for v in free)
    for v in free:                                  # the rest goes to the others, in proportion
        alloc[v] = left * alloc[v] / free_total if free_total else left / len(free)
    return {v: round(alloc[v], 4) for v in ids}, "auto"


def pick(alloc: Mapping[str, float], rng: Optional[random.Random] = None) -> str:
    rng = rng or random.Random()
    x, acc = rng.random(), 0.0
    items = sorted(alloc.items())
    for v, share in items:
        acc += share
        if x < acc:
            return v
    return items[-1][0]


async def refresh(db: Any, campaign: Mapping[str, Any], *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Work the split out again from the campaign's last 30 days of calls, and store it."""
    now = now or datetime.now(timezone.utc)
    ids = list(campaign.get("variant_ids") or [])
    rows = await db[m.ATTEMPTS].find({"campaign_id": campaign["_id"], "status": "done", "created_at": {"$gte": now - WINDOW},
                                      "variant_id": {"$in": ids}},
                                     {"transcript": 0, "gate": 0}).limit(50_000).to_list(50_000)
    stats = ls.arm_stats(ls.lead_outcomes(rows, now=now), key=lambda o: o.variant_id)
    alloc, how = allocation(stats, ids)
    doc = {"_id": f"scripts:{campaign['_id']}", "campaign_id": campaign["_id"], "alloc": alloc, "how": how,
           "p_best": p_best(stats, ids) if len(ids) >= 2 else {}, "stats": {v: s.to_doc() for v, s in stats.items()},
           "variant_ids": ids, "updated_at": now}
    await db[STATS].replace_one({"_id": doc["_id"]}, doc, upsert=True)
    return doc


async def current(db: Any, campaign: Mapping[str, Any], *, now: datetime,
                  cache: Optional[Dict[Any, Any]] = None) -> Dict[str, Any]:
    key = ("scripts", campaign["_id"])
    if cache is not None and key in cache:
        return cache[key]
    doc = await db[STATS].find_one({"_id": f"scripts:{campaign['_id']}"})
    stale = doc is None or (_aware(doc.get("updated_at")) or now) <= now - REFRESH_EVERY \
        or list(doc.get("variant_ids") or []) != list(campaign.get("variant_ids") or [])
    if stale:
        doc = await refresh(db, campaign, now=now)
    if cache is not None:
        cache[key] = doc
    return doc


async def choose(db: Any, campaign: Mapping[str, Any], lead: Mapping[str, Any], *, now: datetime,
                 cache: Optional[Dict[Any, Any]] = None, rng: Optional[random.Random] = None
                 ) -> Optional[Tuple[str, Dict[str, Any]]]:
    """(script, {"how", "p"}) for an automatic split, or None to keep the even hash split."""
    ids = [v for v in campaign.get("variant_ids") or [] if v]
    if campaign.get("script_split") != "auto" or len(ids) < 2 or not campaign.get("offer_id") \
            or campaign.get("mode") == "human_assisted":
        return None
    try:
        mine = lead.get("script_pick") or {}
        if mine.get("campaign_id") == campaign["_id"] and mine.get("variant_id") in ids:
            return str(mine["variant_id"]), {"how": "kept", "p": None}
        doc = await current(db, campaign, now=now, cache=cache)
        alloc = {v: float((doc.get("alloc") or {}).get(v) or 0) for v in ids}
        if sum(alloc.values()) <= 0:
            alloc = {v: 1 / len(ids) for v in ids}
        if doc.get("how") == "even":
            variant = of.pick_variant(ids, str(lead["_id"])) or ids[0]
        else:
            variant = pick(alloc, rng)
        await db[m.LEADS].update_one({"_id": lead["_id"]}, {"$set": {"script_pick": {
            "campaign_id": campaign["_id"], "variant_id": variant, "at": now}}})
        return variant, {"how": doc.get("how") or "even", "p": alloc.get(variant)}
    except Exception:
        logger.warning("Script split failed for lead %s; using the even split", lead.get("_id"), exc_info=True)
        return None
