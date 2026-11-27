# ruff: noqa: F811  (pytest fixtures shared with other test modules are imported, then requested by name)
"""
SALES_LEARNING_PLAN.md Phase 4: live tests that keep what sells and roll back what doesn't. The
policy is pure code, checked at planned points, calibrated by seeded simulation, and applied once.
"""

import random
from datetime import timedelta

import pytest

from config import settings
from core.outbound import experiments as ex
from core.outbound import learning, offers
from core.outbound import models as m
from core.outbound import playbook as pb
from core.outbound import simulate
from core.outbound.learning_stats import ArmStats
from core.outbound.offer_seeds import WEBLAUD_ID
from tests.test_phase4_outbound import NOW
from tests.test_sales_playbook import _consented, entry, offers_db, passing_runner  # noqa: F401


def arm(leads, progress, meetings=0, **kw):
    return ArmStats(leads=leads, progress=progress, meetings=meetings, **kw)


# ── The maths ────────────────────────────────────────────────────────────────

def test_prob_better_matches_a_seeded_monte_carlo():
    rng = random.Random(3)
    for s_a, n_a, s_b, n_b in ((10, 100, 20, 100), (3, 40, 5, 40), (50, 200, 45, 210), (0, 0, 0, 0)):
        draws = 40_000
        wins = sum(rng.betavariate(1 + s_b, 1 + n_b - s_b) > rng.betavariate(1 + s_a, 1 + n_a - s_a) for _ in range(draws))
        assert abs(ex.prob_better(s_a, n_a, s_b, n_b) - wins / draws) < 0.01


def _simulate(p_cp, p_ch, runs, seed):
    """Businesses arrive 1:2 (challenger:champion); decide() at each planned check, as the tick would."""
    rng = random.Random(seed)
    kept = 0
    for _ in range(runs):
        ch, cp, done, prev = ArmStats(), ArmStats(), [], 0
        for look in ex.LOOKS:
            for s, n, p in ((ch, look - prev, p_ch), (cp, 2 * (look - prev), p_cp)):
                for _ in range(n):
                    s.leads += 1
                    if rng.random() < p:
                        s.progress += 1
                        s.meetings += int(rng.random() < 0.4)
            prev = look
            d = ex.decide(ch, cp, looks_done=done, started_at=NOW, now=NOW + timedelta(days=1))
            if d.action in ("promote", "rollback", "stop"):
                kept += d.action == "promote"
                break
            done.append(look)
    return kept / runs


def test_a_change_that_is_no_better_is_rarely_kept():
    assert _simulate(0.20, 0.20, 2000, seed=7) <= 0.10


def test_a_ten_point_lift_is_usually_kept():
    assert _simulate(0.20, 0.30, 600, seed=10) >= 0.80


def test_promotion_needs_meetings_not_to_fall():
    better_steps_fewer_meetings = ex.decide(arm(40, 20, meetings=0), arm(80, 16, meetings=12), looks_done=[], started_at=NOW, now=NOW)
    assert better_steps_fewer_meetings.action != "promote"
    plain = ex.decide(arm(40, 20, meetings=8), arm(80, 16, meetings=6), looks_done=[], started_at=NOW, now=NOW)
    assert plain.action == "promote" and plain.look == 40


def test_checks_happen_only_at_planned_points():
    assert ex.decide(arm(39, 30), arm(78, 5), looks_done=[], started_at=NOW, now=NOW).action == "continue"
    assert ex.decide(arm(41, 30, meetings=10), arm(82, 5, meetings=2), looks_done=[40], started_at=NOW, now=NOW).action == "continue"
    late = ex.decide(arm(90, 30, meetings=10), arm(180, 30, meetings=10), looks_done=[], started_at=NOW, now=NOW)
    assert late.look == 80                                     # a check that came due late uses the later bar


def test_no_clear_winner_keeps_the_champion_at_the_end():
    final = ex.decide(arm(250, 50, meetings=20), arm(500, 100, meetings=40), looks_done=[40, 80, 150], started_at=NOW, now=NOW)
    assert final.action == "stop"
    old = ex.decide(arm(10, 2), arm(20, 4), looks_done=[], started_at=NOW, now=NOW + timedelta(days=46))
    assert old.action == "stop"


@pytest.mark.parametrize("ch,cp,kw,needle", [
    (arm(10, 2, severe_flags=1), arm(20, 4), {}, "fixed rule"),
    (arm(10, 2, other_flags=2, flagged_leads=2), arm(20, 4), {}, "compliance flags"),
    (arm(30, 6, opt_outs=4), arm(60, 12, opt_outs=1), {}, "asked not to be called"),
    (arm(40, 8, early_hangups=16), arm(80, 16, early_hangups=10), {}, "hung up early"),
    (arm(10, 2), arm(20, 4), {"latency_gap_s": 0.5}, "slower"),
    (arm(10, 2), arm(20, 4), {"campaign_optout_paused": True}, "paused itself"),
])
def test_harm_rolls_back_at_once(ch, cp, kw, needle):
    d = ex.decide(ch, cp, looks_done=[], started_at=NOW, now=NOW, **kw)
    assert d.action == "rollback" and needle in d.reason


def test_unchecked_traffic_pauses_then_stops():
    young = ex.decide(arm(5, 1), arm(10, 2), looks_done=[], started_at=NOW, now=NOW + timedelta(hours=10), coverage=0.2)
    assert young.action == "continue"
    paused = ex.decide(arm(5, 1), arm(10, 2), looks_done=[], started_at=NOW, now=NOW + timedelta(hours=50), coverage=0.2)
    assert paused.action == "pause_traffic"
    resumed = ex.decide(arm(5, 1), arm(10, 2), looks_done=[], started_at=NOW, now=NOW + timedelta(hours=60), coverage=0.9,
                        paused=True)
    assert resumed.action == "resume_traffic"
    stopped = ex.decide(arm(5, 1), arm(10, 2), looks_done=[], started_at=NOW, now=NOW + timedelta(hours=80), coverage=0.2,
                        paused=True)
    assert stopped.action == "stop"


def test_arms_are_sticky_salted_and_independent_of_the_script():
    leads = [f"lead_{i}" for i in range(4000)]
    assert all(ex.assign_arm("exp_a", lid, 0.33) == ex.assign_arm("exp_a", lid, 0.33) for lid in leads[:50])
    share = sum(ex.assign_arm("exp_a", lid, 0.33) == "challenger" for lid in leads) / len(leads)
    assert 0.30 < share < 0.36
    by_script = {}
    for lid in leads:
        v = offers.pick_variant(["a", "b"], lid)
        by_script.setdefault(v, []).append(ex.assign_arm("exp_a", lid, 0.33) == "challenger")
    rates = [sum(x) / len(x) for x in by_script.values()]
    assert abs(rates[0] - rates[1]) < 0.05                       # the script never decides the arm
    other = sum(ex.assign_arm("exp_b", lid, 0.33) == ex.assign_arm("exp_a", lid, 0.33) for lid in leads) / len(leads)
    assert other < 0.65                                          # a new test reshuffles


# ── The engine against the database ──────────────────────────────────────────

async def _challenger(db):
    from tests.test_sales_playbook import _live

    champion = await _live(db)
    doc = await pb.create(db, WEBLAUD_ID, [entry(), entry("short", kind="tone", when="they sound rushed", do="keep turns short",
                                                          say="")], parent=champion, changes=[], origin="manual",
                          by="usr_1", now=NOW, go_live=False)
    await pb.queue_tests(db, doc, now=NOW)
    await simulate.tick(db, now=NOW, runner=passing_runner, limit=5)
    await pb.advance(db, now=NOW)
    assert (await pb.get(db, doc["_id"]))["status"] == "ready"
    return champion, doc["_id"]


async def test_a_ready_challenger_starts_a_test_only_on_autopilot(offers_db):
    db = offers_db
    champion, challenger = await _challenger(db)
    await db[pb.LEARNING].update_one({"_id": WEBLAUD_ID}, {"$set": {"mode": "paused"}})
    assert (await learning.experiments_tick(db, now=NOW))["started"] == 0
    await db[pb.LEARNING].update_one({"_id": WEBLAUD_ID}, {"$set": {"mode": "autopilot"}})
    assert (await learning.experiments_tick(db, now=NOW))["started"] == 1
    state = await pb.learning_doc(db, WEBLAUD_ID)
    exp = await db[learning.EXPERIMENTS].find_one({"_id": state["experiment_id"]})
    assert exp["champion_id"] == champion and exp["challenger_id"] == challenger and exp["status"] == "running"
    assert (await pb.get(db, challenger))["status"] == "testing"
    assert await db.audit_log.find_one({"action": "sales.experiment.start", "user_id": "system:learning"})


async def test_dials_enrol_businesses_and_keep_their_arm(offers_db):
    db = offers_db
    _, challenger = await _challenger(db)
    await learning.experiments_tick(db, now=NOW)
    campaign = _consented()
    arms = {}
    for i in range(30):
        lead = {"_id": f"lead_{i}"}
        await db[m.LEADS].insert_one({"_id": lead["_id"], "phone": f"+1512555{i:04d}", "owner": "platform"})
        out = await learning.assign(db, campaign, lead, offer_id=WEBLAUD_ID, offer_version=1, variant_id="ai_automation", now=NOW)
        arms[lead["_id"]] = out["arm"]
        assert out["experiment_id"] and (out["playbook_id"] == challenger) == (out["arm"] == "challenger")
        again = await learning.assign(db, campaign, await db[m.LEADS].find_one({"_id": lead["_id"]}), offer_id=WEBLAUD_ID,
                                      offer_version=1, variant_id="ai_automation", now=NOW)
        assert again["arm"] == out["arm"]                       # sticky
    assert set(arms.values()) == {"champion", "challenger"}


async def test_a_business_already_in_conversation_stays_on_the_champion(offers_db):
    db = offers_db
    champion, _ = await _challenger(db)
    await db[m.LEADS].insert_one({"_id": "lead_old", "phone": "+15125559999", "owner": "platform"})
    await db[m.ATTEMPTS].insert_one({"_id": "att_old", "call_id": "out-att_old", "lead_id": "lead_old", "offer_id": WEBLAUD_ID,
                                     "status": "done", "outcome": "callback", "created_at": NOW - timedelta(days=2)})
    await learning.experiments_tick(db, now=NOW)
    out = await learning.assign(db, _consented(), {"_id": "lead_old"}, offer_id=WEBLAUD_ID, offer_version=1,
                                variant_id="ai_automation", now=NOW)
    assert out["arm"] == "champion" and out["experiment_id"] is None and out["playbook_id"] == champion
    assert (await db[m.LEADS].find_one({"_id": "lead_old"}))["assign"]["enrolled"] is False


async def _record(db, exp_id, arm_name, i, **kw):
    doc = {"_id": f"att_{arm_name}_{i}", "call_id": f"out-{arm_name}-{i}", "lead_id": f"lead_{arm_name}_{i}",
           "offer_id": WEBLAUD_ID, "status": "done", "outcome": "no_conversation", "amd": "human", "events": [],
           "arm": arm_name, "experiment_id": exp_id, "created_at": NOW, "ended_at": NOW,
           "checks": {"severe": [], "other": []}, "review": {"status": "done"}, **kw}
    await db[m.ATTEMPTS].insert_one(doc)


async def test_a_severe_flag_rolls_back_once_audited_and_emailed(offers_db, monkeypatch):
    db = offers_db
    champion, challenger = await _challenger(db)
    await learning.experiments_tick(db, now=NOW)
    exp = await db[learning.EXPERIMENTS].find_one({"status": "running"})
    await _record(db, exp["_id"], "challenger", 1, checks={"severe": ["not_honest_ai"], "other": []})
    sent = []

    async def fake_alert(subject, text):
        sent.append(subject)
        return 1
    from core.outbound import guardrails

    monkeypatch.setattr(guardrails, "alert", fake_alert)
    stats = await learning.experiments_tick(db, now=NOW + timedelta(minutes=5))
    assert stats["decided"] == 1
    assert (await db[learning.EXPERIMENTS].find_one({"_id": exp["_id"]}))["status"] == "rolled_back"
    assert (await pb.get(db, challenger))["status"] == "rolled_back"
    state = await pb.learning_doc(db, WEBLAUD_ID)
    assert state["live_playbook_id"] == champion and state["experiment_id"] is None and state["last_rollback_at"]
    assert sent and "rolled back" in sent[0]
    assert await db.audit_log.count_documents({"action": "sales.experiment.rollback"}) == 1
    second = await learning.apply_decision(db, exp, ex.Decision("rollback", "again"), now=NOW + timedelta(minutes=6))
    assert second is False and await db.audit_log.count_documents({"action": "sales.experiment.rollback"}) == 1


async def test_a_winning_challenger_goes_live(offers_db):
    db = offers_db
    champion, challenger = await _challenger(db)
    await learning.experiments_tick(db, now=NOW)
    exp = await db[learning.EXPERIMENTS].find_one({"status": "running"})
    for i in range(40):
        await _record(db, exp["_id"], "challenger", i, outcome="demo_booked" if i < 24 else "not_interested",
                      events=["meeting"] if i < 24 else [])
    for i in range(80):
        await _record(db, exp["_id"], "champion", i, outcome="demo_booked" if i < 12 else "not_interested",
                      events=["meeting"] if i < 12 else [])
    await learning.experiments_tick(db, now=NOW + timedelta(days=3))
    done = await db[learning.EXPERIMENTS].find_one({"_id": exp["_id"]})
    assert done["status"] == "promoted" and done["decision"]["look"] == 40
    assert (await pb.learning_doc(db, WEBLAUD_ID))["live_playbook_id"] == challenger
    assert (await pb.get(db, champion))["status"] == "retired"


async def test_calls_that_didnt_run_their_arms_playbook_are_left_out(offers_db):
    db = offers_db
    await _challenger(db)
    await learning.experiments_tick(db, now=NOW)
    exp = await db[learning.EXPERIMENTS].find_one({"status": "running"})
    await _record(db, exp["_id"], "challenger", 1, playbook_applied=False, checks={"severe": ["not_honest_ai"], "other": []})
    d = await learning.evaluate(db, exp, now=NOW + timedelta(minutes=5))
    assert d.action == "continue"                               # the flagged call never ran the challenger


async def test_a_person_can_stop_a_test(offers_db):
    db = offers_db
    champion, challenger = await _challenger(db)
    await learning.experiments_tick(db, now=NOW)
    assert await learning.stop_experiment(db, WEBLAUD_ID, reason="Stopped by usr_1", by="usr_1", now=NOW)
    assert (await pb.get(db, challenger))["status"] == "retired"
    assert (await pb.learning_doc(db, WEBLAUD_ID))["live_playbook_id"] == champion
    assert await learning.stop_experiment(db, WEBLAUD_ID, reason="again", by="usr_1", now=NOW) is False


async def test_a_crash_halfway_is_finished_on_the_next_tick(offers_db):
    db = offers_db
    _, challenger = await _challenger(db)
    await learning.experiments_tick(db, now=NOW)
    exp = await db[learning.EXPERIMENTS].find_one({"status": "running"})
    await db[learning.EXPERIMENTS].update_one({"_id": exp["_id"]}, {"$set": {"status": "promoted"}})   # pointer swap never ran
    assert (await learning.experiments_tick(db, now=NOW))["reconciled"] == 1
    state = await pb.learning_doc(db, WEBLAUD_ID)
    assert state["live_playbook_id"] == challenger and state["experiment_id"] is None


async def test_learning_curve_counts_businesses_by_week(offers_db):
    db = offers_db
    for i in range(6):
        await db[m.ATTEMPTS].insert_one({"_id": f"att_c{i}", "call_id": f"out-c{i}", "lead_id": f"l{i}", "offer_id": WEBLAUD_ID,
                                         "status": "done", "amd": "human", "mode": "ai_cold_b2b_us",
                                         "outcome": "demo_booked" if i % 2 else "not_interested",
                                         "events": ["meeting"] if i % 2 else [], "created_at": NOW - timedelta(days=i * 3)})
    curve = await learning.learning_curve(db, WEBLAUD_ID, now=NOW)
    assert sum(p["reached"] for p in curve["points"]) == 6
    assert all(0 <= (p["next_step_rate"] or 0) <= 1 for p in curve["points"])


def test_settings_bound_the_share():
    assert 0.1 <= settings.SALES_EXPERIMENT_SHARE <= 0.5
