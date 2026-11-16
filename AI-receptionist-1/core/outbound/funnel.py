"""
The numbers the dialer is judged on (Roadmap Task 4.6): a funnel from dial to demo, the cost of
each demo, the two personas side by side, and how often answering-machine detection was right.

Pure functions over `call_attempts` documents.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

from core.outbound import models as m

CONVERSATION_SECONDS = 30
_CONNECTED_OUTCOMES = m.HUMAN_OUTCOMES | {"voicemail", "machine", "uncertain"}
_INTERESTED = frozenset({"interested", "callback", "demo_booked"})


def _rate(part: int, whole: int) -> Optional[float]:
    return round(part / whole, 4) if whole else None


def _is_human(a: Mapping[str, Any]) -> bool:
    return a.get("amd") == "human" or a.get("outcome") in m.HUMAN_OUTCOMES


def funnel(attempts: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    rows = [a for a in attempts if a.get("status") == "done" and a.get("outcome") not in m.UNCOUNTED_OUTCOMES]
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


def amd_accuracy(attempts: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Compare the detector's verdict with what a person heard (`amd_label`), human vs not human —
    the split that decides whether the agent speaks or hangs up. Acceptance: ≥ 90 % on 100 calls."""
    labelled = [a for a in attempts if a.get("amd") and a.get("amd_label")]
    correct = sum(1 for a in labelled if (a["amd"] == "human") == (a["amd_label"] == "human"))
    missed_humans = sum(1 for a in labelled if a["amd_label"] == "human" and a["amd"] != "human")
    talked_to_machines = sum(1 for a in labelled if a["amd_label"] != "human" and a["amd"] == "human")
    return {"labelled": len(labelled), "correct": correct, "accuracy": _rate(correct, len(labelled)),
            "hung_up_on_people": missed_humans, "talked_to_machines": talked_to_machines}
