"""
The numbers the dialer is judged on (Roadmap Task 4.6): a funnel from dial to demo, the cost of
each demo, the two personas side by side, and how often answering-machine detection was right.

Pure functions over `call_attempts` documents. "Next steps" are counted the learning loop's way
(core/outbound/learning_stats.py): verified actions only, a callback once it was kept.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional

from core.outbound import learning_stats as ls
from core.outbound import models as m

CONVERSATION_SECONDS = 30
# What a query must fetch for next steps to be counted (learning_stats.credit).
NEXT_STEP_FIELDS: Dict[str, int] = {k: 1 for k in ("lead_id", "created_at", "ended_at", "events", "outcome_set_by",
                                                    "callback_at", "taken_over", "email_consent_at", "hangup_stage")}
_CONNECTED_OUTCOMES = m.HUMAN_OUTCOMES | {"voicemail", "machine", "uncertain"}
_INTERESTED = frozenset({"interested", "callback", "demo_booked"})


def _rate(part: int, whole: int) -> Optional[float]:
    return round(part / whole, 4) if whole else None


def _is_human(a: Mapping[str, Any]) -> bool:
    return a.get("amd") == "human" or a.get("outcome") in m.HUMAN_OUTCOMES


def funnel(attempts: Iterable[Mapping[str, Any]], *, now: Optional[datetime] = None) -> Dict[str, Any]:
    rows = [a for a in attempts if a.get("status") == "done" and a.get("outcome") not in m.UNCOUNTED_OUTCOMES]
    steps = ls.next_step_counts(rows, now=now)
    connected = [a for a in rows if a.get("amd") or a.get("outcome") in _CONNECTED_OUTCOMES]
    human = [a for a in rows if _is_human(a)]
    conversation = [a for a in human if (a.get("duration_sec") or 0) > CONVERSATION_SECONDS]
    interested = [a for a in rows if a.get("outcome") in _INTERESTED]
    demos = [a for a in rows if a.get("outcome") == "demo_booked"]
    cost = round(sum(float(a.get("cost_usd") or 0) for a in rows), 2)
    by_outcome: Dict[str, int] = {}
    for a in rows:
        by_outcome[str(a.get("outcome"))] = by_outcome.get(str(a.get("outcome")), 0) + 1
    return {
        "stages": [
            {"key": "dialled", "label": "Dialled", "count": len(rows)},
            {"key": "connected", "label": "Connected", "count": len(connected)},
            {"key": "human", "label": "A person answered", "count": len(human)},
            {"key": "conversation", "label": f"Talked for over {CONVERSATION_SECONDS} s", "count": len(conversation)},
            {"key": "interested", "label": "Interested", "count": len(interested)},
            {"key": "demo_booked", "label": "Demo booked", "count": len(demos)},
        ],
        "answer_rate": _rate(len(human), len(rows)),
        "demo_rate": _rate(len(demos), len(human)),
        "reached": steps["reached"],
        "next_steps": steps["next_steps"],
        "next_step_rate": _rate(steps["next_steps"], steps["reached"]),
        "opt_outs": by_outcome.get("dnc", 0),
        "opt_out_rate": _rate(by_outcome.get("dnc", 0), len(human)),
        "cost_usd": cost,
        "cost_per_demo_usd": round(cost / len(demos), 2) if demos else None,
        "by_outcome": dict(sorted(by_outcome.items(), key=lambda kv: -kv[1])),
        "by_persona": by_persona(rows),
        # Trial and paid conversions are recorded by billing (Phase 5), not by the dialer.
        "trial": None, "paid": None,
    }


def by_persona(rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """A/B comparison: demo rate is demos per person reached."""
    groups: Dict[str, Dict[str, int]] = {}
    for a in rows:
        g = groups.setdefault(str(a.get("persona_id") or "?"), {"dialled": 0, "human": 0, "demo_booked": 0})
        g["dialled"] += 1
        g["human"] += int(_is_human(a))
        g["demo_booked"] += int(a.get("outcome") == "demo_booked")
    return [{"persona_id": pid, **g, "demo_rate": _rate(g["demo_booked"], g["human"])}
            for pid, g in sorted(groups.items())]


def by_variant(rows: Iterable[Mapping[str, Any]], *, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """A/B comparison by what was actually said: offer, version and script variant."""
    rows = list(rows)
    nexts = ls.next_attempts(rows)
    groups: Dict[tuple, Dict[str, int]] = {}
    for a in rows:
        if a.get("status") != "done" or a.get("outcome") in m.UNCOUNTED_OUTCOMES:
            continue
        key = (str(a.get("offer_id") or "off_laudline"), int(a.get("offer_version") or 0),
               str(a.get("variant_id") or a.get("persona_id") or "?"))
        g = groups.setdefault(key, {"dialled": 0, "human": 0, "demo_booked": 0, "reached": 0, "next_steps": 0})
        g["dialled"] += 1
        g["human"] += int(_is_human(a))
        g["demo_booked"] += int(a.get("outcome") == "demo_booked")
        c = ls.credit(a, next_attempt=nexts.get(str(a.get("_id"))), now=now)
        g["reached"] += int(c.reached)
        g["next_steps"] += int(c.progress is True)
    return [{"offer_id": k[0], "offer_version": k[1], "variant_id": k[2], **g,
             "demo_rate": _rate(g["demo_booked"], g["human"]),
             "next_step_rate": _rate(g["next_steps"], g["reached"])} for k, g in sorted(groups.items())]


def amd_accuracy(attempts: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Compare the detector's verdict with what a person heard (`amd_label`), human vs not human —
    the split that decides whether the agent speaks or hangs up. Acceptance: ≥ 90 % on 100 calls."""
    labelled = [a for a in attempts if a.get("amd") and a.get("amd_label")]
    correct = sum(1 for a in labelled if (a["amd"] == "human") == (a["amd_label"] == "human"))
    missed_humans = sum(1 for a in labelled if a["amd_label"] == "human" and a["amd"] != "human")
    talked_to_machines = sum(1 for a in labelled if a["amd_label"] != "human" and a["amd"] == "human")
    return {"labelled": len(labelled), "correct": correct, "accuracy": _rate(correct, len(labelled)),
            "hung_up_on_people": missed_humans, "talked_to_machines": talked_to_machines}
