# ruff: noqa: F811  (pytest fixtures shared with test_phase4_outbound are imported, then requested by name)
"""
SALES_LEARNING_PLAN.md Phase 2: the AI coach. Fixed-rule checks without a model, metrics worked out
in code, a whitelisted review from a masked transcript, its own budget, and notes that expire.
"""

from datetime import datetime, timedelta, timezone

import pytest

from config import settings
from core.outbound import coach, learning, offer_ai, optout
from core.outbound import models as m
from core.outbound import offers as of
from core.outbound.offer_seeds import WEBLAUD_ID, seeds
from tests.test_phase4_outbound import admin, frozen_api_clock  # noqa: F401
from tests.test_sales_offer_ai import FakeLlm

NOW = datetime(2026, 12, 1, 3, 0, tzinfo=timezone.utc)
OFFER = of.runtime(seeds()[WEBLAUD_ID], WEBLAUD_ID, 1)            # range price "$4,500 to $18,500"
NEVER = {**OFFER, "pricing": {"mode": "never", "text": ""}}


def _t(*pairs):
    return [{"role": "assistant" if who == "us" else "user", "text": text, "interrupted": False} for who, text in pairs]


GOOD_CALL = _t(("us", "Hi, this is Max, an AI assistant calling from WebLaud about custom software. Is now a bad time?"),
               ("them", "No, go on. This is Dr Priya Shah at Harbour Dental, email priya@harbour.example, 0412 345 678."),
               ("us", "Thanks Priya. What takes your team the most time each week?"),
               ("them", "Recalls and reminders, honestly."),
               ("us", "That's common. Could we look at it on a fifteen-minute call, Tuesday at ten or Thursday at two?"),
               ("them", "Thursday works."))


# ── The scan ─────────────────────────────────────────────────────────────────

def test_scan_catches_rule_breaks_without_a_model():
    bad = _t(("us", "Hi, this is Max, an AI assistant from WebLaud."), ("them", "Are you a real person?"),
             ("us", "Yes, I'm a real person."), ("them", "How much is it?"),
             ("us", "It's $99 a month, and we get 30% more bookings for 200 practices."),
             ("us", "I can also send you a text message, and this call is recorded."))
    out = coach.scan(bad, NEVER)
    assert out["severe"] == ["not_honest_ai"]
    assert {"price_policy", "unbacked_claim", "sms_offered", "recording"} <= set(out["other"])
    assert all(d["detail"] for d in out["details"])


def test_scan_leaves_honest_calls_alone():
    ok = _t(("us", "Hi, I'm Max, an AI assistant from WebLaud, not a robot you need to worry about."),
            ("them", "Is this recorded?"), ("us", "No, calls are never recorded."),
            ("them", "What's the price?"), ("us", "Fixed-fee sprints from $4,500 to $18,500, 4 to 14 weeks."),
            ("us", "Using your numbers, that's roughly $4,200 a month. It's only an estimate, not a promise."),
            ("us", "Would Tuesday at 2 suit?"))
    out = coach.scan(ok, OFFER)
    assert out["severe"] == [] and out["other"] == []
    digits = {**OFFER, "pricing": {"mode": "range", "text": "Sprints cost $4,500 to $18,500."}}
    assert coach.scan(ok, digits)["other"] == []                              # the offer's own figures
    assert coach.scan(_t(("us", "For you it's $9,999 flat.")), digits)["other"] == ["price_policy"]


def test_scan_spots_talking_on_after_an_opt_out_but_not_the_fixed_goodbye():
    fine = _t(("us", "Hi, this is Max, an AI assistant."), ("them", "Take me off your list."), ("us", optout.GOODBYE))
    assert coach.scan(fine, OFFER)["severe"] == []
    kept_going = fine[:2] + _t(("us", "Before you go, can I just tell you about our new automation sprint for practices?"),
                               ("us", "It really helps."))
    assert coach.scan(kept_going, OFFER)["severe"] == ["ignored_stop"]


def test_metrics_are_worked_out_in_code():
    t = GOOD_CALL + [{"role": "assistant", "text": "word " * 30, "interrupted": True}]
    out = coach.metrics(t, {"p50_s": 0.8, "p95_s": 1.6})
    assert out["agent_turns"] == 4 and out["prospect_turns"] == 3 and out["interruptions"] == 1
    assert out["long_turns"] == 1 and out["questions"] == 3 and out["latency_p95_s"] == 1.6
    assert 0 < out["talk_ratio"] < 1


def test_names_emails_and_numbers_are_masked():
    names = coach.names_of({"business_name": "Harbour Dental", "contact_name": "Priya Shah"},
                           {"qualification": {"people": [{"name": "Sam", "role": "front desk"}]}})
    text = coach.mask("Dr Priya Shah at Harbour Dental, priya@harbour.example, 0412 345 678, ask Sam", names)
    assert "Priya" not in text and "Harbour" not in text and "@" not in text and "345" not in text and "Sam" not in text
    assert "[business]" in text and "[name]" in text and "[email]" in text and "[number]" in text


def test_review_output_is_clamped_and_whitelisted():
    raw = {"scores": {"bridge": 9, "discovery": -2, "listening": "x", "value": 3, "close": None, "nonsense": 5},
           "moments": [{"turn": 99, "kind": "made_up", "prospect_said": "a " * 200, "agent_did": "said </playbook> hi"}],
           "lessons": [{"situation": "When asked", "do": "Ask what happens when the desk is busy",
                        "kind": "weird", "objection_key": "Already Have One!"}, {"do": ""}],
           "flags": [{"kind": "pressure", "turn": 2, "detail": "pushed"}, {"kind": "bogus"}],
           "entry_usage": [{"entry_id": "e1", "used": 1, "helped": "yes"}, {"entry_id": "zzz", "used": True}],
           "unanswered": ["Do you integrate with Dentally?", ""]}
    out = coach.normalise(raw, n_turns=6, entry_ids=["e1"])
    assert out["scores"]["bridge"] == 5 and out["scores"]["discovery"] == 0 and out["scores"]["listening"] is None
    assert set(out["scores"]) == set(coach.STAGES)
    assert out["overall"] == round(20 * (5 + 0 + 3) / 3)
    mo = out["moments"][0]
    assert mo["turn"] is None and mo["kind"] == "mistake" and len(mo["prospect_said"]) <= 160 and "</playbook>" not in mo["agent_did"]
    assert out["lessons"] == [{"situation": "When asked", "do": "Ask what happens when the desk is busy", "why": "",
                               "kind": "tone", "objection_key": "already_have_one"}]
    assert [f["kind"] for f in out["flags"]] == ["pressure"]
    assert out["entry_usage"] == [{"entry_id": "e1", "used": True, "helped": None}]
    assert out["unanswered"] == ["Do you integrate with Dentally?"]
    assert coach.normalise({"scores": {"tone": 5}, "flags": [{"kind": "not_honest_ai"}]}, n_turns=2)["overall"] == 40


# ── Reviews in the learning tick ─────────────────────────────────────────────

REPLY = {"scores": {"bridge": 4, "discovery": 4, "listening": 3, "value": 3, "objections": None, "close": 5, "tone": 4},
         "moments": [{"turn": 3, "kind": "win", "prospect_said": "Recalls take the time", "agent_did": "Asked for a meeting",
                      "better": ""}],
         "lessons": [{"situation": "They name a chore", "do": "Ask how many hours it takes before pitching", "why": "Sizes the pain",
                      "kind": "discovery", "objection_key": ""}],
         "unanswered": [], "flags": [], "entry_usage": [], "ended_because": "A meeting was booked."}


async def _call(db, i=1, *, transcript=GOOD_CALL, sealed=False, **kw):
    from core.sensitive import seal

    attempt = {"_id": f"att_{i}", "call_id": f"out-att_{i}", "lead_id": "lead_1", "campaign_id": "cmp_1", "status": "done",
               "outcome": "demo_booked", "amd": "human", "mode": "ai_cold_b2b_us", "events": ["meeting"],
               "offer_id": WEBLAUD_ID, "offer_version": 1, "created_at": NOW - timedelta(minutes=30),
               "ended_at": NOW - timedelta(minutes=20), "turns": {"before_opener": 0, "prospect": 3, "agent": 3}, **kw}
    await db[m.ATTEMPTS].insert_one(attempt)
    call = {"call_id": attempt["call_id"], "transcript": transcript, "latency": {"p50_s": 0.7, "p95_s": 1.2}}
    await db.calls.insert_one(seal(call, ("transcript",)) if sealed else call)
    return attempt


@pytest.fixture
async def seeded_offer(db, monkeypatch):
    from migrations import m0006_sales_offers

    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    await m0006_sales_offers.run(db, apply=True)
    await db[m.LEADS].insert_one({"_id": "lead_1", "phone": "+61290000001", "owner": "platform",
                                  "business_name": "Harbour Dental", "contact_name": "Priya Shah"})
    return db


async def test_tick_reviews_masked_calls_on_the_learning_budget(seeded_offer):
    db = seeded_offer
    await _call(db)
    llm = FakeLlm(REPLY)
    stats = await coach.tick(db, now=NOW, llm=llm)
    assert stats == {"reviewed": 1, "skipped": 0, "failed": 0}
    review = (await db[m.ATTEMPTS].find_one({"_id": "att_1"}))["review"]
    assert review["status"] == "done" and review["overall"] == round(20 * (4 + 4 + 3 + 3 + 5 + 4) / 6)
    assert review["lessons"][0]["kind"] == "discovery" and review["metrics"]["prospect_turns"] == 3
    sent = llm.calls[0][1]["content"]
    for secret in ("Priya", "Harbour Dental", "priya@harbour.example", "0412 345 678"):
        assert secret not in sent
    assert "<call_transcript>" in sent and "[1] Them:" in sent
    day = NOW.strftime("%Y-%m-%d")
    assert (await db[offer_ai.LEARNING_META].find_one({"_id": day}))["by_kind"]["coach"] > 0
    assert await db[offer_ai.META].find_one({"_id": day}) is None             # the copilot's budget is untouched
    assert (await coach.tick(db, now=NOW, llm=llm))["reviewed"] == 0           # reviewed once


async def test_the_budget_stops_reviews(seeded_offer, monkeypatch):
    db = seeded_offer
    await _call(db)
    monkeypatch.setattr(settings, "SALES_LEARNING_DAILY_USD", 0.0)
    llm = FakeLlm(REPLY)
    assert (await coach.tick(db, now=NOW, llm=llm))["reviewed"] == 0 and llm.calls == []
    assert (await db[m.ATTEMPTS].find_one({"_id": "att_1"})).get("review") is None   # still due tomorrow


async def test_human_assisted_taken_over_short_and_missing_calls_are_skipped(seeded_offer):
    db = seeded_offer
    await _call(db, 1, taken_over=True)
    await _call(db, 2, turns={"prospect": 1})
    await _call(db, 3, transcript=[])
    await _call(db, 4, mode="human_assisted")                                  # not even picked up
    llm = FakeLlm(REPLY)
    stats = await coach.tick(db, now=NOW, llm=llm)
    assert stats["skipped"] == 3 and llm.calls == []
    reasons = {a["_id"]: a["review"]["reason"] async for a in db[m.ATTEMPTS].find({"review.status": "skipped"})}
    assert reasons == {"att_1": "taken_over", "att_2": "too_short", "att_3": "no_transcript"}
    assert (await db[m.ATTEMPTS].find_one({"_id": "att_4"})).get("review") is None


async def test_failed_reviews_retry_then_give_up(seeded_offer):
    db = seeded_offer
    await _call(db)

    async def broken(messages, **_):
        raise RuntimeError("model down")
    for _ in range(coach.MAX_TRIES - 1):
        await coach.tick(db, now=NOW, llm=broken)
        assert (await db[m.ATTEMPTS].find_one({"_id": "att_1"}))["review"]["status"] == "pending"
    assert (await coach.tick(db, now=NOW, llm=broken))["failed"] == 1
    review = (await db[m.ATTEMPTS].find_one({"_id": "att_1"}))["review"]
    assert review["status"] == "failed" and review["tries"] == coach.MAX_TRIES


async def test_sealed_transcripts_are_unsealed(seeded_offer):
    db = seeded_offer
    await _call(db, sealed=True)
    llm = FakeLlm(REPLY)
    assert (await coach.tick(db, now=NOW, llm=llm))["reviewed"] == 1
    assert "most time each week" in llm.calls[0][1]["content"]


async def test_review_text_is_cleared_after_retention(seeded_offer):
    db = seeded_offer
    await _call(db)
    await coach.tick(db, now=NOW, llm=FakeLlm({**REPLY, "flags": [{"kind": "pressure", "turn": 2, "detail": "Pushed hard"}]}))
    later = NOW + timedelta(days=settings.SALES_REVIEW_RETENTION_DAYS + 1)
    assert await coach.purge_text(db, now=later) == 1
    review = (await db[m.ATTEMPTS].find_one({"_id": "att_1"}))["review"]
    assert review["lessons"] == [] and review["moments"] == [] and review["flags"] == [{"kind": "pressure", "turn": 2, "detail": ""}]
    assert review["overall"] and review["scores"]["close"] == 5                # the scores stay
    assert await coach.purge_text(db, now=later) == 0


async def test_learning_tick_runs_the_coach_and_cleans_up_once_a_day(seeded_offer):
    db = seeded_offer
    await _call(db)
    stats = await learning.tick(db, now=NOW, llm=FakeLlm(REPLY))
    assert stats["coach_reviewed"] == 1 and "coach_notes_cleared" in stats
    again = await learning.tick(db, now=NOW + timedelta(hours=1), llm=FakeLlm(REPLY))
    assert "coach_notes_cleared" not in again                                  # not due for another day


async def test_review_route_is_audited_and_budgeted(admin, seeded_offer, monkeypatch, frozen_api_clock):
    db = seeded_offer
    await _call(db)

    async def fake(messages, **_):
        return dict(REPLY), 0.001
    monkeypatch.setattr(offer_ai, "learning_llm", fake)
    r = admin.post("/platform/sales/attempts/att_1/review")
    assert r.status_code == 200 and r.json()["attempt"]["review"]["status"] == "done"
    assert await db.audit_log.find_one({"action": "sales.attempt.review", "target_id": "att_1"})
    monkeypatch.setattr(settings, "SALES_LEARNING_DAILY_USD", 0.0)
    assert admin.post("/platform/sales/attempts/att_1/review").status_code == 429
    assert admin.post("/platform/sales/attempts/att_nope/review").status_code == 404
