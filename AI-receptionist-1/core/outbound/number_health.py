"""
Caller ID health (SALES_SUITE_PLAN.md Phase 12): how each outbound number is doing, and resting the
ones that look flagged as spam.

For each `from` number over the last 7 days: dials, answer rate, the share answered by a person,
short hang-ups (a person picks up and is gone within 5 seconds, a sign of a "Spam likely" label), and
opt-outs. A number is `suspect` when, with at least MIN_DIALS dials, its answer rate is under half
the median of the other numbers in its country, or it fell by more than half against the week before.

A suspect number is rested for REST_HOURS, but only while another healthy number for the same
country exists: the dialer is never left without a caller ID. `dialer.pick_from_number` tries
rested numbers last, so one is used only when every healthy number has reached its daily volume. A
person can rest or reactivate a number by hand (audited).
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

from core.outbound import models as m
from core.outbound import phones

STATE = "sales_number_state"
WINDOW = timedelta(days=7)
MIN_DIALS = 30
REST_HOURS = 72
SHORT_HANGUP_S = 5
_CONNECTED = m.HUMAN_OUTCOMES | {"voicemail", "machine", "uncertain"}


def _country(e164: str) -> str:
    info = phones.parse(e164)
    return str(getattr(info, "country", "") or "") if info else ""


def stats_for(rows: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    done = [a for a in rows if a.get("status") == "done" and a.get("outcome") not in m.UNCOUNTED_OUTCOMES]
    dials = len(done)
    connected = sum(1 for a in done if a.get("amd") or a.get("outcome") in _CONNECTED)
    humans = [a for a in done if a.get("amd") == "human" or a.get("outcome") in m.HUMAN_OUTCOMES]
    short = sum(1 for a in humans if a.get("duration_sec") is not None and float(a["duration_sec"]) < SHORT_HANGUP_S)
    return {"dials": dials, "answer_rate": round(connected / dials, 4) if dials else None,
            "human_rate": round(len(humans) / dials, 4) if dials else None,
            "short_hangup_rate": round(short / len(humans), 4) if humans else None,
            "opt_outs": sum(1 for a in done if a.get("outcome") == "dnc")}


def flag(numbers: Dict[str, Dict[str, Any]], previous: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """number → why it looks flagged. Pure."""
    out: Dict[str, str] = {}
    by_country: Dict[str, List[str]] = {}
    for n in numbers:
        by_country.setdefault(_country(n), []).append(n)
    for n, s in numbers.items():
        if s["dials"] < MIN_DIALS or s["answer_rate"] is None:
            continue
        peers = [numbers[p]["answer_rate"] for p in by_country[_country(n)]
                 if p != n and numbers[p]["dials"] >= MIN_DIALS and numbers[p]["answer_rate"] is not None]
        if peers and s["answer_rate"] < 0.5 * statistics.median(peers):
            out[n] = (f"answers {s['answer_rate'] * 100:.0f}% of calls against {statistics.median(peers) * 100:.0f}% "
                      "for your other numbers in that country")
            continue
        before = previous.get(n) or {}
        if (before.get("dials") or 0) >= MIN_DIALS and before.get("answer_rate") and s["answer_rate"] < 0.5 * before["answer_rate"]:
            out[n] = f"answer rate fell from {before['answer_rate'] * 100:.0f}% to {s['answer_rate'] * 100:.0f}% this week"
    return out


async def _attempts(db: Any, since: datetime, until: datetime) -> Dict[str, List[Dict[str, Any]]]:
    rows = await db[m.ATTEMPTS].find({"created_at": {"$gte": since, "$lt": until}, "from": {"$nin": [None, ""]}},
                                     {"from": 1, "status": 1, "outcome": 1, "amd": 1, "duration_sec": 1}).to_list(100_000)
    out: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(str(r["from"]), []).append(r)
    return out


async def campaign_numbers(db: Any) -> List[str]:
    rows = await db[m.CAMPAIGNS].find({"status": {"$ne": "completed"}}, {"from_numbers": 1}).to_list(500)
    return sorted({n for r in rows for n in r.get("from_numbers") or []})


async def health(db: Any, *, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    this_week = await _attempts(db, now - WINDOW, now)
    last_week = await _attempts(db, now - 2 * WINDOW, now - WINDOW)
    numbers = sorted(set(this_week) | set(await campaign_numbers(db)))
    current = {n: stats_for(this_week.get(n, [])) for n in numbers}
    previous = {n: stats_for(last_week.get(n, [])) for n in numbers}
    flags = flag(current, previous)
    states = {d["_id"]: d for d in await db[STATE].find({"_id": {"$in": numbers}}).to_list(500)}
    out = []
    for n in numbers:
        st = states.get(n) or {}
        rested = st.get("rested_until")
        if isinstance(rested, datetime) and rested.tzinfo is None:
            rested = rested.replace(tzinfo=timezone.utc)
        out.append({"number": n, "country": _country(n), **current[n], "previous": previous[n],
                    "suspect": flags.get(n, ""), "rested_until": rested if isinstance(rested, datetime) and rested > now else None,
                    "rest_reason": st.get("reason", "") if isinstance(rested, datetime) and rested > now else ""})
    return out


async def rested(db: Any, numbers: Iterable[str], now: datetime) -> set[str]:
    rows = await db[STATE].find({"_id": {"$in": list(numbers)}, "rested_until": {"$gt": now}}, {"_id": 1}).to_list(500)
    return {r["_id"] for r in rows}


async def rest(db: Any, number: str, *, hours: int = REST_HOURS, reason: str, by: str, now: Optional[datetime] = None) -> None:
    now = now or datetime.now(timezone.utc)
    await db[STATE].update_one({"_id": number}, {"$set": {"rested_until": now + timedelta(hours=hours), "reason": reason,
                                                          "by": by, "at": now}}, upsert=True)


async def reactivate(db: Any, number: str, *, by: str, now: Optional[datetime] = None) -> None:
    now = now or datetime.now(timezone.utc)
    await db[STATE].update_one({"_id": number}, {"$set": {"rested_until": None, "reason": "", "by": by, "at": now}}, upsert=True)


async def auto_rest(db: Any, *, now: Optional[datetime] = None) -> Dict[str, int]:
    """Rest suspect numbers when a healthy one for the same country is left. Emails the team once each."""
    from core.outbound import guardrails

    now = now or datetime.now(timezone.utc)
    rows = await health(db, now=now)
    healthy = {r["number"] for r in rows if not r["suspect"] and not r["rested_until"]}
    stats = {"rested": 0}
    for r in rows:
        if not r["suspect"] or r["rested_until"]:
            continue
        if not any(_country(h) == r["country"] for h in healthy if h != r["number"]):
            continue                                       # never leave a country without a caller ID
        await rest(db, r["number"], reason=f"Looks flagged: {r['suspect']}", by="system:number_health", now=now)
        healthy.discard(r["number"])
        stats["rested"] += 1
        await guardrails._system_audit(db, "sales.number.auto_rest", "phone", r["number"][-4:], {"reason": r["suspect"]})
        await guardrails.alert(f"Caller ID rested: …{r['number'][-4:]}",
                               f"{r['number']} {r['suspect']}, so it is resting for {REST_HOURS} hours. Your other numbers "
                               "keep calling. Check its spam labels (and register US numbers with the free caller "
                               "registry), then reactivate it from Sales dialer → Compliance → Numbers.")
    return stats
