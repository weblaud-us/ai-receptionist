"""
What a sales call achieved, in the terms the learning loop judges by (SALES_LEARNING_PLAN.md Phase 0).

Pure functions over `call_attempts` documents. The rules that keep the numbers honest:

- **Only verified next steps count.** A meeting the booking tool made, details sent with their OK
  (an email that was sent or queued — never one an unsubscribe suppressed — or WhatsApp), a transfer
  to a person, or a callback that was *kept*. The model saying "interested" through `end_call` is
  not a next step. A person relabelling a call always wins.
- **A callback counts once it is kept:** the business's next call reached a person. Until
  `callback_at` + CALLBACK_GRACE it is pending (None); after that, with no one reached, it didn't
  happen.
- **The unit is the business, not the call.** A lead that took a callback and then booked counts
  once, with its best conversation, so an approach that farms callbacks gains nothing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from core.outbound import models as m

EVENTS: tuple[str, ...] = ("meeting", "info_email", "info_whatsapp", "callback", "transfer")
DETAIL_EVENTS = frozenset({"info_email", "info_whatsapp", "transfer"})
CALLBACK_GRACE = timedelta(days=2)
DEFAULT_CALLBACK_AFTER = timedelta(hours=24)        # what outcomes.DEFAULT_CALLBACK_DELAY gives a callback
EARLY_PROSPECT_TURNS = 2                            # "early": they hung up after one or two replies
HANGUP_STAGES: tuple[str, ...] = ("opener", "early", "later")
PERSON_PROGRESS = frozenset({"interested", "demo_booked"})
# Compliance flags that end a live test at once (core/outbound/coach.py raises them; the scan too).
SEVERE_FLAGS = frozenset({"ignored_stop", "not_honest_ai"})


def _aware(dt: Any) -> Optional[datetime]:
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── At the end of a call ─────────────────────────────────────────────────────

def turn_counts(transcript: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    """Spoken turns: what they said before our opener, what they said after it, and ours (the opener
    is our first item)."""
    before = prospect = agent = 0
    for item in transcript:
        if not str(item.get("text") or "").strip():
            continue
        if item.get("role") == "assistant":
            agent += 1
        elif item.get("role") == "user":
            if agent:
                prospect += 1
            else:
                before += 1
    return {"before_opener": before, "prospect": prospect, "agent": agent}


def hangup_stage(turns: Mapping[str, int], ended_by: Optional[str]) -> Optional[str]:
    """When the prospect hung up: during the opener (no reply to it), early (one or two replies), or
    later. None when they didn't hang up first (we ended it, or something else did)."""
    if ended_by != "prospect":
        return None
    said = int(turns.get("prospect") or 0)
    if said == 0:
        return "opener"
    return "early" if said <= EARLY_PROSPECT_TURNS else "later"


def shutdown_fields(*, transcript: Sequence[Mapping[str, Any]], ended_by: Optional[str], events: Sequence[str],
                    info_status: Optional[str], callback_at: Optional[datetime],
                    whatsapp_consent_at: Optional[datetime], transferred: bool, taken_over: bool) -> Dict[str, Any]:
    """What the sales agent stores on the attempt when a call ends, for the learning loop."""
    turns = turn_counts(transcript)
    return {
        "ended_by": ended_by, "turns": turns, "hangup_stage": hangup_stage(turns, ended_by),
        "events": [e for e in dict.fromkeys(events) if e in EVENTS],
        "info_status": info_status, "callback_at": callback_at, "whatsapp_consent_at": whatsapp_consent_at,
        "transferred": bool(transferred), "taken_over": bool(taken_over),
    }


# ── What one call achieved ───────────────────────────────────────────────────

def verified_events(a: Mapping[str, Any]) -> List[str]:
    """The next steps a tool confirmed. Calls from before the events were recorded fall back to
    what their outcome can prove: a booked meeting, a callback, an email they agreed to."""
    if "events" in a and a.get("events") is not None:
        return [e for e in a.get("events") or [] if e in EVENTS]
    outcome = a.get("outcome")
    out: List[str] = []
    if outcome == "demo_booked":
        out.append("meeting")
    elif outcome == "callback":
        out.append("callback")
    elif outcome == "interested" and a.get("email_consent_at"):
        out.append("info_email")
    return out


def is_reached(a: Mapping[str, Any]) -> bool:
    """A person at the business picked up and the agent spoke for us (not a wrong number, not a
    machine, not a dial that never happened, not a call a person took over)."""
    if a.get("status") != "done":
        return False
    outcome = a.get("outcome")
    if outcome in m.UNCOUNTED_OUTCOMES or outcome == "wrong_number" or a.get("taken_over"):
        return False
    return a.get("amd") == "human" or outcome in m.HUMAN_OUTCOMES


@dataclass(frozen=True)
class Credit:
    reached: bool
    progress: Optional[bool]            # None: a callback that may still be kept
    meeting: bool = False
    opt_out: bool = False
    early_hangup: bool = False          # hung up after one or two replies, with nothing agreed
    opener_hangup: bool = False         # hung up without answering the opener


NOT_REACHED = Credit(reached=False, progress=False)


def callback_kept(a: Mapping[str, Any], next_attempt: Optional[Mapping[str, Any]], now: datetime) -> Optional[bool]:
    if next_attempt is not None and is_reached(next_attempt):
        return True
    due = _aware(a.get("callback_at")) or ((_aware(a.get("ended_at")) or now) + DEFAULT_CALLBACK_AFTER)
    return False if now > due + CALLBACK_GRACE else None


def credit(a: Mapping[str, Any], *, next_attempt: Optional[Mapping[str, Any]] = None,
           now: Optional[datetime] = None) -> Credit:
    """What this call achieved. `next_attempt` is the same business's next call, if any."""
    now = now or datetime.now(timezone.utc)
    if not is_reached(a):
        return NOT_REACHED
    outcome = a.get("outcome")
    by_person = bool(a.get("outcome_set_by"))
    meeting = outcome == "demo_booked"
    opt_out = outcome == "dnc"
    stage = a.get("hangup_stage")
    progress: Optional[bool]
    events = set(verified_events(a))
    if opt_out:
        progress = False
    elif by_person:                              # a person's label wins, both ways
        if outcome in PERSON_PROGRESS:
            progress = True
        elif outcome == "callback":
            progress = callback_kept(a, next_attempt, now)
        else:
            progress = False
    elif meeting or events & DETAIL_EVENTS:
        progress = True
    elif "callback" in events or (outcome == "callback" and "events" not in a):
        progress = callback_kept(a, next_attempt, now)
    else:
        progress = False
    return Credit(reached=True, progress=progress, meeting=meeting, opt_out=opt_out,
                  early_hangup=stage == "early" and progress is False,
                  opener_hangup=stage == "opener")


def next_attempts(attempts: Iterable[Mapping[str, Any]]) -> Dict[str, Mapping[str, Any]]:
    """attempt id → the same lead's next attempt, from the rows given (in time order)."""
    by_lead: Dict[str, List[Mapping[str, Any]]] = {}
    for a in attempts:
        if a.get("lead_id"):
            by_lead.setdefault(str(a["lead_id"]), []).append(a)
    out: Dict[str, Mapping[str, Any]] = {}
    for rows in by_lead.values():
        rows.sort(key=lambda r: _aware(r.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc))
        for cur, nxt in zip(rows, rows[1:]):
            out[str(cur.get("_id"))] = nxt
    return out


# ── Per business, per arm ────────────────────────────────────────────────────

def _flags(a: Mapping[str, Any]) -> tuple[int, int]:
    """(severe, other) compliance flags on a call: the deterministic scan plus the coach's review."""
    severe = other = 0
    checks = a.get("checks") or {}
    severe += len(checks.get("severe") or [])
    other += len(checks.get("other") or [])
    review = a.get("review") or {}
    for f in review.get("flags") or []:
        if not isinstance(f, Mapping):
            continue
        if f.get("kind") in SEVERE_FLAGS:
            severe += 1
        else:
            other += 1
    return severe, other


@dataclass(frozen=True)
class LeadOutcome:
    lead_id: str
    arm: Optional[str]
    variant_id: str
    progress: Optional[bool]
    meeting: bool
    opt_out: bool
    early_hangup: bool
    opener_hangup: bool
    severe_flags: int
    other_flags: int
    conversations: int
    reviewed: int


def lead_outcomes(attempts: Sequence[Mapping[str, Any]], *, now: Optional[datetime] = None, only_applied: bool = False,
                  followers: Optional[Mapping[str, Mapping[str, Any]]] = None) -> List[LeadOutcome]:
    """Each business reached, judged by its best conversation in `attempts`. `followers` adds the
    next attempt of callbacks whose follow-up call falls outside `attempts`. `only_applied` keeps
    only calls that really ran with the playbook their arm assigned."""
    now = now or datetime.now(timezone.utc)
    nexts = {**(followers or {}), **next_attempts(attempts)}
    grouped: Dict[str, List[Mapping[str, Any]]] = {}
    for a in attempts:
        if only_applied and a.get("playbook_applied") is False:
            continue
        if not is_reached(a):
            continue
        grouped.setdefault(str(a.get("lead_id") or a.get("_id")), []).append(a)
    out: List[LeadOutcome] = []
    for lead_id, rows in grouped.items():
        credits = [credit(r, next_attempt=nexts.get(str(r.get("_id"))), now=now) for r in rows]
        if any(c.progress is True for c in credits):
            progress: Optional[bool] = True
        elif any(c.progress is None for c in credits):
            progress = None
        else:
            progress = False
        flags = [_flags(r) for r in rows]
        first = rows[0]
        out.append(LeadOutcome(
            lead_id=lead_id, arm=first.get("arm"),
            variant_id=str(first.get("variant_id") or first.get("persona_id") or "?"),
            progress=progress, meeting=any(c.meeting for c in credits), opt_out=any(c.opt_out for c in credits),
            early_hangup=progress is not True and any(c.early_hangup for c in credits),
            opener_hangup=any(c.opener_hangup for c in credits),
            severe_flags=sum(s for s, _ in flags), other_flags=sum(o for _, o in flags), conversations=len(rows),
            reviewed=sum(1 for r in rows if (r.get("review") or {}).get("status") == "done")))
    return out


@dataclass
class ArmStats:
    leads: int = 0               # businesses reached
    progress: int = 0            # … that took a real next step
    pending: int = 0             # … whose only next step is a callback not yet due
    meetings: int = 0
    opt_outs: int = 0
    early_hangups: int = 0
    opener_hangups: int = 0
    severe_flags: int = 0
    other_flags: int = 0
    conversations: int = 0
    reviewed: int = 0
    flagged_leads: int = 0       # businesses with at least one flag of any kind

    @property
    def decided(self) -> int:
        """Businesses whose result is known (pending callbacks are left out until they resolve)."""
        return self.leads - self.pending

    def rate(self, part: int, whole: Optional[int] = None) -> Optional[float]:
        whole = self.decided if whole is None else whole
        return round(part / whole, 4) if whole else None

    def to_doc(self) -> Dict[str, Any]:
        doc = asdict(self)
        doc["decided"] = self.decided
        doc["next_step_rate"] = self.rate(self.progress)
        doc["meeting_rate"] = self.rate(self.meetings, self.leads)
        doc["opt_out_rate"] = self.rate(self.opt_outs, self.leads)
        return doc


def arm_stats(outcomes: Iterable[LeadOutcome], key: Callable[[LeadOutcome], Optional[str]]) -> Dict[str, ArmStats]:
    out: Dict[str, ArmStats] = {}
    for o in outcomes:
        k = key(o)
        if k is None:
            continue
        s = out.setdefault(k, ArmStats())
        s.leads += 1
        s.progress += int(o.progress is True)
        s.pending += int(o.progress is None)
        s.meetings += int(o.meeting)
        s.opt_outs += int(o.opt_out)
        s.early_hangups += int(o.early_hangup)
        s.opener_hangups += int(o.opener_hangup)
        s.severe_flags += o.severe_flags
        s.other_flags += o.other_flags
        s.conversations += o.conversations
        s.reviewed += o.reviewed
        s.flagged_leads += int(bool(o.severe_flags or o.other_flags))
    return out


def next_step_counts(rows: Sequence[Mapping[str, Any]], *, now: Optional[datetime] = None) -> Dict[str, int]:
    """Calls reached and calls with a verified next step, for the funnel (pending callbacks don't
    count yet)."""
    now = now or datetime.now(timezone.utc)
    nexts = next_attempts(rows)
    reached = next_steps = 0
    for a in rows:
        c = credit(a, next_attempt=nexts.get(str(a.get("_id"))), now=now)
        reached += int(c.reached)
        next_steps += int(c.progress is True)
    return {"reached": reached, "next_steps": next_steps}
