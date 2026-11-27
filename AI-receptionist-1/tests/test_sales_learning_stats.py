"""
SALES_LEARNING_PLAN.md Phase 0: the signals the learning loop judges by. Only verified next steps
count, callbacks count once kept, the unit is the business, and the prompt keeps its cacheable
prefix.
"""

from datetime import datetime, timedelta, timezone

from core.outbound import funnel, insights, learning_stats as ls, persona, timing
from core.outbound import models as m
from core.outbound.offer_seeds import WEBLAUD_ID, seeds
from tests.test_sales_offer_ai import FakeLlm

NOW = datetime(2026, 12, 1, 3, 0, tzinfo=timezone.utc)


def _a(i, lead="lead_1", **kw):
    doc = {"_id": f"att_{i}", "call_id": f"out-att_{i}", "lead_id": lead, "status": "done",
           "outcome": "no_conversation", "amd": "human",
           "created_at": NOW - timedelta(days=10) + timedelta(hours=i), "ended_at": NOW - timedelta(days=10),
           "events": [], "offer_id": WEBLAUD_ID, "offer_version": 1, "variant_id": "a"}
    doc.update(kw)
    return doc


# ── End of call ──────────────────────────────────────────────────────────────

def test_turns_and_hangup_stage_tell_the_opener_apart_from_later():
    opener_only = [{"role": "user", "text": "Hello?"}, {"role": "assistant", "text": "Hi, this is Max, an AI…"}]
    turns = ls.turn_counts(opener_only)
    assert turns == {"before_opener": 1, "prospect": 0, "agent": 1}
    assert ls.hangup_stage(turns, "prospect") == "opener"
    two = opener_only + [{"role": "user", "text": "Not now."}, {"role": "assistant", "text": "Sure."},
                         {"role": "user", "text": "Bye"}]
    assert ls.hangup_stage(ls.turn_counts(two), "prospect") == "early"
    long = two + [{"role": "user", "text": "Well actually"}]
    assert ls.hangup_stage(ls.turn_counts(long), "prospect") == "later"
    assert ls.hangup_stage(ls.turn_counts(long), "agent") is None        # we ended it: not a hang-up


def test_shutdown_fields_keep_only_known_events_once():
    out = ls.shutdown_fields(transcript=[], ended_by="agent", events=["meeting", "meeting", "made_up"],
                             info_status=None, callback_at=None, whatsapp_consent_at=None, transferred=False,
                             taken_over=False)
    assert out["events"] == ["meeting"] and out["hangup_stage"] is None and out["ended_by"] == "agent"


# ── What counts ──────────────────────────────────────────────────────────────

def test_progress_counts_only_verified_next_steps():
    claimed = _a(1, outcome="interested")                                   # end_call(interested), nothing done
    assert ls.credit(claimed, now=NOW).progress is False
    suppressed = _a(2, outcome="interested", info_status="suppressed", email_consent_at=NOW)
    assert ls.credit(suppressed, now=NOW).progress is False
    sent = _a(3, outcome="interested", events=["info_email"], info_status="sent")
    assert ls.credit(sent, now=NOW).progress is True
    whatsapp = _a(4, outcome="interested", events=["info_whatsapp"])
    assert ls.credit(whatsapp, now=NOW).progress is True
    booked = _a(5, outcome="demo_booked", events=["meeting"])
    c = ls.credit(booked, now=NOW)
    assert c.progress is True and c.meeting
    opted_out = _a(6, outcome="dnc", events=["meeting"])
    assert ls.credit(opted_out, now=NOW).progress is False and ls.credit(opted_out, now=NOW).opt_out


def test_a_person_relabel_wins_both_ways():
    relabelled = _a(1, outcome="interested", outcome_set_by="usr_ana")
    assert ls.credit(relabelled, now=NOW).progress is True
    corrected = _a(2, outcome="not_interested", outcome_set_by="usr_ana", events=["info_email"])
    assert ls.credit(corrected, now=NOW).progress is False                 # the person said it wasn't
    cb = _a(3, outcome="callback", outcome_set_by="usr_ana", callback_at=NOW + timedelta(days=1))
    assert ls.credit(cb, now=NOW).progress is None                         # their callback still has to be kept


def test_a_callback_counts_only_when_it_is_kept():
    cb = _a(1, outcome="callback", events=["callback"], callback_at=NOW - timedelta(days=9))
    kept = _a(2, outcome="not_interested")                                  # we reached them again
    missed = _a(2, outcome="no_answer", amd=None)
    assert ls.credit(cb, next_attempt=kept, now=NOW).progress is True
    assert ls.credit(cb, next_attempt=missed, now=NOW).progress is False   # due + 2 days passed, nobody reached
    soon = _a(3, outcome="callback", events=["callback"], callback_at=NOW + timedelta(hours=5))
    assert ls.credit(soon, now=NOW).progress is None                       # pending
    assert ls.credit(_a(4, outcome="callback", events=["callback"], callback_at=NOW - timedelta(days=1)),
                     now=NOW).progress is None                             # still inside the grace period


def test_reached_excludes_wrong_numbers_machines_takeovers_and_uncounted():
    assert ls.is_reached(_a(1))
    assert not ls.is_reached(_a(2, outcome="wrong_number"))
    assert not ls.is_reached(_a(3, outcome="voicemail", amd="machine-vm"))
    assert not ls.is_reached(_a(4, taken_over=True))
    assert not ls.is_reached(_a(5, outcome="blocked_by_gate", amd=None))
    assert not ls.is_reached(_a(6, status="in_progress"))


def test_a_business_counts_once_with_its_best_conversation():
    rows = [_a(1, outcome="callback", events=["callback"], callback_at=NOW - timedelta(days=9)),
            _a(2, outcome="demo_booked", events=["meeting"]),
            _a(3, lead="lead_2", outcome="not_interested")]
    outs = {o.lead_id: o for o in ls.lead_outcomes(rows, now=NOW)}
    assert len(outs) == 2 and outs["lead_1"].progress is True and outs["lead_1"].meeting
    assert outs["lead_1"].conversations == 2 and outs["lead_2"].progress is False
    stats = ls.arm_stats(outs.values(), key=lambda o: "all")["all"]
    assert stats.leads == 2 and stats.progress == 1 and stats.meetings == 1 and stats.decided == 2


def test_pending_callbacks_are_left_out_of_the_rate():
    rows = [_a(1, outcome="callback", events=["callback"], callback_at=NOW + timedelta(days=1)),
            _a(2, lead="lead_2", outcome="demo_booked", events=["meeting"])]
    s = ls.arm_stats(ls.lead_outcomes(rows, now=NOW), key=lambda o: "x")["x"]
    assert s.pending == 1 and s.decided == 1 and s.to_doc()["next_step_rate"] == 1.0


def test_opener_and_early_hangups_are_told_apart():
    rows = [_a(1, hangup_stage="opener"), _a(2, lead="lead_2", hangup_stage="early"),
            _a(3, lead="lead_3", hangup_stage="early", outcome="demo_booked", events=["meeting"])]
    outs = {o.lead_id: o for o in ls.lead_outcomes(rows, now=NOW)}
    assert outs["lead_1"].opener_hangup and not outs["lead_1"].early_hangup
    assert outs["lead_2"].early_hangup
    assert not outs["lead_3"].early_hangup                                   # they booked: not a hang-up loss


def test_flags_from_the_scan_and_the_coach_are_counted():
    row = _a(1, checks={"severe": ["not_honest_ai"], "other": ["long_turns"]},
             review={"status": "done", "flags": [{"kind": "pressure"}, {"kind": "ignored_stop"}]})
    out = ls.lead_outcomes([row], now=NOW)[0]
    assert out.severe_flags == 2 and out.other_flags == 2 and out.reviewed == 1


def test_unapplied_calls_can_be_excluded():
    rows = [_a(1, playbook_applied=False, outcome="demo_booked", events=["meeting"]), _a(2, lead="lead_2")]
    assert {o.lead_id for o in ls.lead_outcomes(rows, now=NOW, only_applied=True)} == {"lead_2"}


def test_old_calls_without_events_fall_back_to_their_outcome():
    assert ls.verified_events({"outcome": "demo_booked"}) == ["meeting"]
    assert ls.verified_events({"outcome": "interested", "email_consent_at": NOW}) == ["info_email"]
    assert ls.verified_events({"outcome": "interested"}) == []


# ── Funnel and insights ──────────────────────────────────────────────────────

def test_funnel_and_variants_report_next_steps():
    rows = [_a(1, outcome="interested"), _a(2, lead="lead_2", outcome="demo_booked", events=["meeting"]),
            _a(3, lead="lead_3", outcome="interested", events=["info_email"], variant_id="b")]
    f = funnel.funnel(rows, now=NOW)
    assert f["reached"] == 3 and f["next_steps"] == 2 and f["next_step_rate"] == round(2 / 3, 4)
    by = {v["variant_id"]: v for v in funnel.by_variant(rows, now=NOW)}
    assert by["a"]["next_steps"] == 1 and by["a"]["reached"] == 2 and by["b"]["next_step_rate"] == 1.0
    assert "demo_rate" in by["a"]                                            # existing keys unchanged


async def test_insights_read_the_reason_and_filter_by_version(db, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    offer = {**seeds()[WEBLAUD_ID], "id": WEBLAUD_ID, "version": 2}
    llm = FakeLlm({"objections": [], "suggestions": {}})
    for i in range(6):
        await db[m.ATTEMPTS].insert_one(_a(i, lead=f"lead_{i}", outcome="not_interested", reason="No budget this year",
                                           created_at=NOW - timedelta(days=1), offer_version=2))
    await db[m.ATTEMPTS].insert_one(_a(9, lead="lead_9", outcome="not_interested", reason="Old version reason",
                                       created_at=NOW - timedelta(days=1), offer_version=1))
    out = await insights.insights(db, offer, now=NOW, llm=llm, version=2)
    assert out["answered"] == 6 and out["version"] == 2
    sent = llm.calls[0][1]["content"]
    assert "not interested: No budget this year" in sent and "Old version reason" not in sent


# ── Prompt and dial ──────────────────────────────────────────────────────────

def test_prompt_keeps_campaign_notes_before_the_clock():
    offer = {**seeds()[WEBLAUD_ID], "id": WEBLAUD_ID, "version": 1}
    variant = offer["variants"][0]
    lead = {"_id": "l", "business_name": "Harbour Dental", "timezone": "Australia/Sydney"}
    campaign = {"mode": "ai_cold_b2b_us", "notes": "Mention the free audit."}
    a = persona.build_prompt(offer, variant, lead, campaign, now=NOW)
    b = persona.build_prompt(offer, variant, lead, campaign, now=NOW + timedelta(hours=7))
    assert a.split("# Right now")[0] == b.split("# Right now")[0]          # the cacheable prefix is identical
    assert a.index("<campaign_notes>") < a.index("# Right now") and a.count("</campaign_notes>") == 1


def test_local_slot_is_the_leads_own_weekday_and_hour():
    slot = timing.local_slot({"timezone": "Australia/Sydney"}, NOW)       # 03:00 UTC = 14:00 in Sydney (AEDT)
    assert slot == {"tz": "Australia/Sydney", "dow": 2, "hour": 14}
    assert timing.local_slot({}, NOW) is None
