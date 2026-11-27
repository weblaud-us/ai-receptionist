# ruff: noqa: F811  (pytest fixtures shared with test_phase4_outbound are imported, then requested by name)
"""
SALES_SUITE_PLAN.md wave 2: automated test calls, campaign quick-setup, the launch checklist,
auto-pause guardrails and caller ID health. Test calls use a fake runner (the real one needs
livekit-agents and runs in tests/evals).
"""

from datetime import timedelta

from config import settings
from core.outbound import campaign_setup, dialer, guardrails, number_health, offer_ai, preflight, simulate
from core.outbound import models as m
from core.outbound import offers as of
from core.outbound.offer_seeds import WEBLAUD_ID, seeds
from tests.test_phase4_outbound import NOW, admin, campaign_doc, frozen_api_clock, lead_doc, ready, seeded  # noqa: F401
from tests.test_sales_offers import _seed_offers


def _attempt(i, **kw):
    return {"_id": f"att_{i}", "call_id": f"out-{i}", "campaign_id": "cmp_1", "status": "done", "outcome": "no_answer", "amd": None,
            "from": "+15125550100", "created_at": NOW - timedelta(hours=1), "cost_usd": 0.05, **kw}


# ── Automated test calls ─────────────────────────────────────────────────────

def test_scenarios_follow_the_offers_own_price_policy_objections_and_proof():
    weblaud = of.runtime(seeds()[WEBLAUD_ID], WEBLAUD_ID, 1)
    sc = {s.id: s for s in simulate.scenarios(weblaud)}
    assert {"ai_honesty", "not_interested", "stop_calling", "price", "callback", "injection", "claims"} <= set(sc)
    assert sc["not_interested"].must_call == ["mark_not_interested"] and sc["not_interested"].must_not_call == ["add_to_dnc"]
    assert "four and a half" in sc["price"].intent                       # judged against the offer's own sentence
    assert sc["objection_1"].turns[-1] == "We already have a developer"
    assert "LaudLine" in sc["claims"].intent
    never = {**weblaud, "pricing": {"mode": "never", "text": ""}, "proof_points": []}
    assert "Does not state any price" in {s.id: s for s in simulate.scenarios(never)}["price"].intent


async def test_queued_test_calls_run_and_a_pass_counts_for_the_published_version(db, monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    await _seed_offers(db)
    snap = await of.get_version(db, WEBLAUD_ID, 1)
    sim = await simulate.queue(db, offer_id=WEBLAUD_ID, settings_doc=snap["settings"], variant_ids=[], version=None,
                               by="usr_1", now=NOW)
    assert sim["checksum"] == snap["checksum"] and sim["variant_ids"] == ["ai_automation", "custom_software"]

    async def runner(offer, variant_id, scenario):
        ok = not (variant_id == "custom_software" and scenario.id == "price")
        return simulate.ScenarioResult(scenario.id, scenario.label, ok, "fine" if ok else "said a number", [], [])

    stats = await simulate.tick(db, now=NOW, runner=runner)
    assert stats["ran"] == 1
    done = await db[simulate.SIMULATIONS].find_one({"_id": sim["_id"]})
    assert done["status"] == "done" and done["passed"] == done["total"] - 1
    assert await simulate.latest_pass(db, snap["checksum"], ["ai_automation"]) is None   # one scenario failed
    assert (await db[offer_ai.META].find_one({}))["by_kind"]["simulation"] > 0

    again = await simulate.queue(db, offer_id=WEBLAUD_ID, settings_doc=snap["settings"], variant_ids=["ai_automation"],
                                 version=1, by="usr_1", now=NOW)

    async def all_pass(offer, variant_id, scenario):
        return simulate.ScenarioResult(scenario.id, scenario.label, True, "fine", [], [])
    await simulate.tick(db, now=NOW, runner=all_pass)
    assert (await simulate.latest_pass(db, snap["checksum"], ["ai_automation"]))["_id"] == again["_id"]
    assert await simulate.latest_pass(db, snap["checksum"], ["ai_automation", "custom_software"]) is None


async def test_without_livekit_a_test_call_says_it_cannot_run_here(db, monkeypatch):
    await _seed_offers(db)
    snap = await of.get_version(db, WEBLAUD_ID, 1)
    await simulate.queue(db, offer_id=WEBLAUD_ID, settings_doc=snap["settings"], variant_ids=[], version=1, by="u", now=NOW)
    monkeypatch.setattr(simulate, "livekit_available", lambda: False)
    assert (await simulate.tick(db, now=NOW))["failed"] == 1
    assert "can't run on this server" in (await db[simulate.SIMULATIONS].find_one({}))["error"]


async def test_the_simulate_route_queues_once_and_lists_results(admin, db, monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    await _seed_offers(db)
    res = admin.post(f"/platform/sales/offers/{WEBLAUD_ID}/simulate", json={"version": 1})
    assert res.status_code == 200, res.text
    assert "Keeps to the price policy" in res.json()["scenarios"]
    assert admin.post(f"/platform/sales/offers/{WEBLAUD_ID}/simulate", json={"version": 1}).status_code == 409
    listed = admin.get(f"/platform/sales/offers/{WEBLAUD_ID}/simulations").json()["simulations"]
    assert listed[0]["status"] == "queued"
    assert await db.audit_log.find_one({"action": "sales.offer.simulate"})


# ── Launch checklist ─────────────────────────────────────────────────────────

async def test_start_is_blocked_until_the_test_calls_pass(admin, db, ready, monkeypatch, frozen_api_clock):
    monkeypatch.setattr(settings, "SALES_REQUIRE_SIMULATION", True)
    await _seed_offers(db)
    await db[m.CAMPAIGNS].insert_one(campaign_doc(status="draft", offer_id=WEBLAUD_ID, offer_version=1,
                                                  variant_ids=["ai_automation"], legal_review=None))
    blocked = admin.post("/platform/sales/campaigns/cmp_1/start", json={"legal_review_confirmed": True})
    assert blocked.status_code == 409 and "preflight_failed" in blocked.text and "Test calls passed" in blocked.text
    checks = {c["id"]: c for c in admin.get("/platform/sales/campaigns/cmp_1/preflight").json()["checks"]}
    assert checks["simulation"]["status"] == "fail" and checks["leads"]["status"] == "warn"

    snap = await of.get_version(db, WEBLAUD_ID, 1)
    await db[simulate.SIMULATIONS].insert_one({"_id": "sim_ok", "offer_id": WEBLAUD_ID, "checksum": snap["checksum"],
                                               "variant_ids": ["ai_automation"], "status": "done", "passed": 11,
                                               "total": 11, "created_at": NOW, "finished_at": NOW})
    started = admin.post("/platform/sales/campaigns/cmp_1/start", json={"legal_review_confirmed": True})
    assert started.status_code == 200, started.text
    campaign = await db[m.CAMPAIGNS].find_one({"_id": "cmp_1"})
    assert campaign["legal_review"]["preflight"] and campaign["status"] == "running"


# ── Campaign quick-setup ─────────────────────────────────────────────────────

async def test_quick_setup_groups_leads_by_the_call_type_the_rules_allow(admin, db, frozen_api_clock, monkeypatch):
    monkeypatch.setattr(settings, "SALES_AI_COLD_COUNTRIES", "US")
    monkeypatch.setattr(settings, "SALES_HUMAN_ASSISTED_COUNTRIES", "US,GB,AU")
    await _seed_offers(db)
    await db[m.CAMPAIGNS].insert_one(campaign_doc(_id="cmp_old", status="completed", from_numbers=["+15125550100", "+61290000001"]))
    for doc in (lead_doc(status="new", campaign_id=None, source="expo"),
                lead_doc("+61 2 9374 4000", _id="lead_au", status="new", campaign_id=None, source="expo", vertical="dental"),
                lead_doc("+44 20 7946 0000", _id="lead_gb", status="new", campaign_id=None, source="expo"),
                lead_doc("+61 2 9374 4001", _id="lead_cool", status="new", campaign_id=None, source="expo",
                         cooldown_until=NOW + timedelta(days=30))):
        await db[m.LEADS].insert_one(doc)
    res = admin.post("/platform/sales/campaigns/suggest", json={"offer_id": WEBLAUD_ID, "source": "expo"})
    assert res.status_code == 200, res.text
    out = res.json()
    drafts = {d["mode"]: d for d in out["drafts"]}
    assert drafts["ai_cold_b2b_us"]["countries"] == ["US"] and drafts["ai_cold_b2b_us"]["from_numbers"] == ["+15125550100"]
    assert drafts["human_assisted"]["countries"] == ["AU", "GB"]
    assert drafts["human_assisted"]["from_numbers"] == ["+61290000001"]
    assert any("AI cold calls are off" in r for r in drafts["human_assisted"]["reasons"])
    assert any("Do Not Call Register" in w for w in drafts["human_assisted"]["warnings"])
    assert drafts["ai_cold_b2b_us"]["variant_ids"] == ["ai_automation", "custom_software"]
    assert drafts["ai_cold_b2b_us"]["daily_cap"] == 50
    assert any("cooling down" in w for w in out["warnings"])
    assert await db[m.CAMPAIGNS].count_documents({}) == 1                 # nothing created


def test_the_suggested_window_is_inside_every_countrys_legal_hours():
    w = campaign_setup.window_for(["GB", "US"])
    assert w["start"] >= "09:30" and w["end"] <= "16:30" and w["days"] == [1, 2, 3, 4, 5]


# ── Auto-pause guardrails ────────────────────────────────────────────────────

def test_guardrails_wait_for_a_sample_then_trip():
    g = m.Guardrails().model_dump()
    few = [_attempt(i, outcome="dnc", amd="human") for i in range(3)]
    assert guardrails.evaluate(g, few) == []                              # three calls are not a trend
    humans = [_attempt(i, outcome="dnc" if i < 2 else "not_interested", amd="human") for i in range(12)]
    assert [t.code for t in guardrails.evaluate(g, humans)] == ["opt_outs"]
    silent = [_attempt(i) for i in range(45)]
    assert [t.code for t in guardrails.evaluate(g, silent)] == ["answer_rate"]
    broken = [_attempt(i, outcome="failed") for i in range(25)]
    assert "errors" in [t.code for t in guardrails.evaluate(g, broken)]
    assert guardrails.evaluate({**g, "enabled": False}, broken) == []
    pricey = [_attempt(i, outcome="voicemail", amd="machine", cost_usd=1.0) for i in range(10)]
    assert [t.code for t in guardrails.evaluate({**g, "max_cost_per_meeting_usd": 5}, pricey)] == ["cost"]


async def test_a_tripped_campaign_pauses_once_audits_and_emails(seeded, monkeypatch):
    db = seeded
    monkeypatch.setattr(settings, "SALES_ALERT_EMAILS", "ops@example.test")
    sent = []

    async def fake_send(to, subject, text, *a, **kw):
        sent.append((to, subject))
        return True
    monkeypatch.setattr("core.integrations.email.send_email", fake_send)
    for i in range(12):
        await db[m.ATTEMPTS].insert_one(_attempt(i, outcome="dnc" if i < 3 else "not_interested", amd="human"))
    assert (await guardrails.check_all(db, now=NOW))["paused"] == 1
    c = await db[m.CAMPAIGNS].find_one({"_id": "cmp_1"})
    assert c["status"] == "paused" and c["paused_reason"]["code"] == "opt_outs"
    assert await db.audit_log.find_one({"action": "sales.campaign.auto_pause"})
    assert sent == [("ops@example.test", "Sales campaign paused: Austin plumbers")]
    assert (await guardrails.check_all(db, now=NOW + timedelta(minutes=5)))["paused"] == 0
    assert len(sent) == 1


# ── Caller ID health ─────────────────────────────────────────────────────────

async def test_a_flagged_number_rests_only_while_another_one_works(seeded, monkeypatch):
    db = seeded
    monkeypatch.setattr(settings, "SALES_ALERT_EMAILS", "")
    monkeypatch.setattr(settings, "SALES_MAX_CALLS_PER_NUMBER_PER_DAY", 1000)
    good, bad = "+15125550100", "+15125550101"
    await db[m.CAMPAIGNS].update_one({"_id": "cmp_1"}, {"$set": {"from_numbers": [good, bad]}})
    for i in range(40):
        await db[m.ATTEMPTS].insert_one(_attempt(f"g{i}", **{"from": good, "outcome": "not_interested", "amd": "human"}
                                                 if i % 2 else {"from": good}))
        await db[m.ATTEMPTS].insert_one(_attempt(f"b{i}", **{"from": bad} if i else {"from": bad, "outcome": "voicemail",
                                                                                       "amd": "machine"}))
    rows = {r["number"]: r for r in await number_health.health(db, now=NOW)}
    assert rows[bad]["suspect"] and not rows[good]["suspect"]
    assert (await number_health.auto_rest(db, now=NOW))["rested"] == 1
    assert await number_health.rested(db, [good, bad], NOW) == {bad}
    # the dialer prefers the healthy number, and uses the rested one only when nothing else is left
    lead = await db[m.LEADS].find_one({})
    campaign = await db[m.CAMPAIGNS].find_one({"_id": "cmp_1"})
    assert await dialer.pick_from_number(db, campaign, lead, NOW) == good
    assert await dialer.pick_from_number(db, {**campaign, "from_numbers": [bad]}, lead, NOW) == bad


async def test_the_last_number_for_a_country_is_never_rested(db):
    bad = "+15125550101"
    await db[m.CAMPAIGNS].insert_one(campaign_doc(from_numbers=[bad]))
    for i in range(40):
        await db[m.ATTEMPTS].insert_one(_attempt(i, **{"from": bad}))
    assert (await number_health.auto_rest(db, now=NOW))["rested"] == 0


async def test_numbers_can_be_rested_and_reactivated_by_hand(admin, db, frozen_api_clock):
    assert admin.post("/platform/sales/numbers/+15125550100/rest", json={"hours": 24}).status_code == 200
    assert await number_health.rested(db, ["+15125550100"], NOW) == {"+15125550100"}
    assert admin.post("/platform/sales/numbers/+15125550100/reactivate").status_code == 200
    assert await number_health.rested(db, ["+15125550100"], NOW) == set()
    assert admin.post("/platform/sales/numbers/nonsense/rest", json={}).status_code == 422


async def test_preflight_warns_about_resting_and_missing_caller_ids(seeded):
    db = seeded
    await number_health.rest(db, "+15125550100", reason="flagged", by="u", now=NOW)
    items = {c["id"]: c for c in await preflight.checks(db, await db[m.CAMPAIGNS].find_one({}), now=NOW)}
    assert items["caller_ids"]["status"] == "warn" and "resting" in items["caller_ids"]["detail"]
    assert items["offer"]["status"] == "warn"                            # the built-in scripts: no test calls to require
    assert preflight.failed(list(items.values())) == []

