"""
Live tests of a new playbook against the current one (SALES_LEARNING_PLAN.md Phase 4). Pure
functions: the policy is in code, like core/outbound/rules.py, never in a model.

How a test is judged:

- **The unit is the business reached**, scored by its best conversation during the test
  (core/outbound/learning_stats.py): a real next step, a meeting, an opt-out, an early hang-up.
- **Planned checks, not a check every minute.** Looking at the numbers after every call and stopping
  the first time they look good makes a false win several times likelier. So the "keep it" decision
  is taken only at LOOKS (challenger businesses decided), each with a stricter bar early on
  (THRESHOLDS). A seeded simulation (tests/test_sales_experiments.py) holds the chance of keeping a
  change that is really no better at 10% or less, and of keeping a real +10-point lift at 80% or more.
- **The exact probability**: P(challenger's rate > champion's) for Beta(1, 1) priors, from Evan
  Miller's closed form with log-gamma. Deterministic, so two ticks always agree.
- **Meetings must not get worse**: "any next step" could otherwise trade meetings for emails.
- **Harm stops a test at once**, whatever the planned checks: a severe compliance flag, more flags
  than the champion, opt-outs, hang-ups after the opener, slower replies, or a campaign that paused
  itself for opt-outs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import exp, lgamma, log
from typing import Any, Dict, List, Literal, Optional, Sequence

from core.outbound.learning_stats import ArmStats

LOOKS: tuple[int, ...] = (40, 80, 150, 250)
# Calibrated by seeded simulation (1:2 split, 10–30% base rates): a change that is really no better is
# kept about 8% of the time; a real +10-point lift about 93%; a +5-point lift about half the time.
THRESHOLDS: tuple[float, ...] = (0.995, 0.99, 0.975, 0.95)
MEETINGS_WORSE_MAX = 0.8          # keep a challenger only while P(fewer meetings) stays below this
MEETINGS_TIE_BREAK = 0.975        # at the last check: inconclusive next steps, clearly more meetings → keep
OPT_OUT_MIN, OPT_OUT_RATIO, OPT_OUT_P = 3, 2.0, 0.9
OPT_OUT_ABS_MIN_LEADS = 20
EARLY_MIN, EARLY_P, EARLY_GAP = 30, 0.95, 0.08
LATENCY_GAP_S = 0.3
OTHER_FLAGS_MIN = 2
COVERAGE_MIN = 0.7
COVERAGE_PAUSE_AFTER = timedelta(hours=48)
COVERAGE_STOP_AFTER = timedelta(hours=72)

Action = Literal["continue", "promote", "rollback", "stop", "pause_traffic", "resume_traffic"]


def _lbeta(a: float, b: float) -> float:
    return lgamma(a) + lgamma(b) - lgamma(a + b)


def prob_better(s_a: int, n_a: int, s_b: int, n_b: int) -> float:
    """P(rate_B > rate_A) with Beta(1 + s, 1 + n − s) for each arm (s successes out of n)."""
    s_a, n_a, s_b, n_b = max(0, int(s_a)), max(0, int(n_a)), max(0, int(s_b)), max(0, int(n_b))
    s_a, s_b = min(s_a, n_a), min(s_b, n_b)
    a_a, b_a, a_b, b_b = 1 + s_a, 1 + n_a - s_a, 1 + s_b, 1 + n_b - s_b
    total = 0.0
    for i in range(a_b):
        total += exp(_lbeta(a_a + i, b_a + b_b) - log(b_b + i) - _lbeta(1 + i, b_b) - _lbeta(a_a, b_a))
    return min(1.0, max(0.0, total))


def assign_arm(experiment_id: str, lead_id: str, share: float) -> str:
    """The same business always gets the same arm in a test. Salted with the test's id, so it is
    independent of the script split (offers.pick_variant hashes the lead id alone)."""
    digest = hashlib.sha256(f"pb:{experiment_id}:{lead_id}".encode("utf-8")).digest()
    return "challenger" if int.from_bytes(digest[:8], "big") / 2 ** 64 < share else "champion"


def _rate(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str
    look: Optional[int] = None
    snapshot: Dict[str, Any] = field(default_factory=dict)


def guards(ch: ArmStats, cp: ArmStats, *, latency_gap_s: Optional[float] = None, campaign_optout_paused: bool = False,
           max_optout_rate: float = 0.05) -> List[str]:
    """Why the challenger must stop now (empty = no harm seen)."""
    out = []
    if ch.severe_flags > 0:
        out.append("A test call broke a fixed rule (it claimed to be a person, or kept going after “don't call”).")
    if ch.other_flags >= OTHER_FLAGS_MIN and _rate(ch.flagged_leads, ch.leads) > _rate(cp.flagged_leads, cp.leads):
        out.append(f"{ch.other_flags} compliance flags on calls with the new playbook, more often than with the current one.")
    if ch.opt_outs >= OPT_OUT_MIN:
        more = _rate(ch.opt_outs, ch.leads) >= OPT_OUT_RATIO * _rate(cp.opt_outs, cp.leads) and \
            prob_better(cp.opt_outs, cp.leads, ch.opt_outs, ch.leads) >= OPT_OUT_P
        too_many = ch.leads >= OPT_OUT_ABS_MIN_LEADS and _rate(ch.opt_outs, ch.leads) > max_optout_rate
        if more or too_many:
            out.append(f"More people asked not to be called with the new playbook ({ch.opt_outs} of {ch.leads}).")
    if ch.leads >= EARLY_MIN and cp.leads >= EARLY_MIN:
        gap = _rate(ch.early_hangups, ch.leads) - _rate(cp.early_hangups, cp.leads)
        if gap >= EARLY_GAP and prob_better(cp.early_hangups, cp.leads, ch.early_hangups, ch.leads) >= EARLY_P:
            out.append(f"More people hung up early with the new playbook ({round(gap * 100)} points more).")
    if latency_gap_s is not None and latency_gap_s > LATENCY_GAP_S:
        out.append(f"Replies were slower with the new playbook (+{latency_gap_s:.2f}s at p95).")
    if campaign_optout_paused:
        out.append("A campaign paused itself for opt-outs during the test.")
    return out


def snapshot(ch: ArmStats, cp: ArmStats) -> Dict[str, Any]:
    return {"champion": cp.to_doc(), "challenger": ch.to_doc(),
            "p_better": round(prob_better(cp.progress, cp.decided, ch.progress, ch.decided), 4),
            # Same population as next steps: businesses whose outcome is known (no callback pending).
            "p_meetings_worse": round(prob_better(ch.meetings, ch.decided, cp.meetings, cp.decided), 4)}


def next_look(looks_done: Sequence[int]) -> Optional[int]:
    return next((x for x in LOOKS if x not in looks_done), None)


def decide(ch: ArmStats, cp: ArmStats, *, looks_done: Sequence[int], started_at: datetime, now: datetime,
           coverage: float = 1.0, latency_gap_s: Optional[float] = None, campaign_optout_paused: bool = False,
           max_days: int = 45, max_optout_rate: float = 0.05, paused: bool = False) -> Decision:
    """What to do with a running test right now."""
    snap = snapshot(ch, cp)
    harm = guards(ch, cp, latency_gap_s=latency_gap_s, campaign_optout_paused=campaign_optout_paused,
                  max_optout_rate=max_optout_rate)
    if harm:
        return Decision("rollback", harm[0], snapshot={**snap, "guards": harm})
    age = now - started_at
    if coverage < COVERAGE_MIN and age > COVERAGE_PAUSE_AFTER:
        if age > COVERAGE_STOP_AFTER and paused:
            return Decision("stop", "Too few of the new playbook's calls could be checked, so the test stopped.",
                            snapshot={**snap, "coverage": coverage})
        if not paused:
            return Decision("pause_traffic", "Too few of the new playbook's calls have been checked yet: it rests "
                                             "until the checks catch up.", snapshot={**snap, "coverage": coverage})
    elif paused and coverage >= COVERAGE_MIN:
        return Decision("resume_traffic", "The checks caught up.", snapshot={**snap, "coverage": coverage})
    due = [x for x in LOOKS if ch.decided >= x and x not in looks_done]
    if due:
        look = due[-1]                                       # a check that came due late uses the later bar
        idx = LOOKS.index(look)
        p_better = snap["p_better"]
        p_meet_worse = snap["p_meetings_worse"]
        final = idx == len(LOOKS) - 1
        if p_better >= THRESHOLDS[idx] and p_meet_worse < MEETINGS_WORSE_MAX:
            return Decision("promote", f"Likely better ({round(p_better * 100)}% sure) after {ch.decided} businesses: "
                                       f"{_pct(ch)} took a next step against {_pct(cp)}.", look=look, snapshot=snap)
        if 1 - p_better >= THRESHOLDS[idx]:
            return Decision("rollback", f"Likely worse ({round((1 - p_better) * 100)}% sure) after {ch.decided} "
                                        f"businesses: {_pct(ch)} took a next step against {_pct(cp)}.", look=look,
                            snapshot=snap)
        if final:
            p_meet_better = prob_better(cp.meetings, cp.leads, ch.meetings, ch.leads)
            if p_meet_better >= MEETINGS_TIE_BREAK:
                return Decision("promote", f"No clear difference in next steps, but more meetings "
                                           f"({round(p_meet_better * 100)}% sure).", look=look, snapshot=snap)
            return Decision("stop", "No clear winner after the last check: the current playbook stays.", look=look,
                            snapshot=snap)
        return Decision("continue", f"Check {idx + 1} of {len(LOOKS)}: too early to tell.", look=look, snapshot=snap)
    if age >= timedelta(days=max_days):
        return Decision("stop", f"No clear winner after {max_days} days: the current playbook stays.", snapshot=snap)
    nxt = next_look(looks_done)
    return Decision("continue", f"Next check at {nxt} businesses reached with the new playbook." if nxt
                    else "Waiting for the last check.", snapshot=snap)


def _pct(s: ArmStats) -> str:
    rate = s.rate(s.progress)
    return "—" if rate is None else f"{round(rate * 100)}%"
