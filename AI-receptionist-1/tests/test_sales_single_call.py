# ruff: noqa: F811  (pytest fixtures shared with test_phase4_outbound are imported, then requested by name)
"""SALES_SINGLE_CALL_PLAN.md: call one business now, without a campaign. The AI talks, after a person
reviewed exactly that call. The gate still decides; a no-answer puts the lead back where it was."""

from datetime import datetime, timedelta, timezone

import pytest

from config import settings
from core.outbound import (
    dialer, followup, gate, offer_ai, outcomes, persona, registry, research, rules, simulate, single_call,
)
from core.outbound import models as m
from core.outbound import offers as of
from core.outbound.gate import GateFacts
from core.outbound.offer_seeds import WEBLAUD_ID, WEBSITES_ID
from migrations import m0006_sales_offers, m0009_sales_website_offer
from tests.test_phase4_outbound import (  # noqa: F401  (fixtures)
    AUSTIN, NOW, Dispatched, admin, campaign_doc, frozen_api_clock, lead_doc, ready, seeded,
)

CALLER_ID = "+15125550100"                    # the running campaign's number: single calls may use it too
NEW_PHONE = "+15125550123"


@pytest.fixture(autouse=True)
def rules_as_shipped(monkeypatch):
    """The defaults in config.py, whatever the local .env says; simulations not required unless a test asks."""
    monkeypatch.setattr(settings, "SALES_AI_COLD_COUNTRIES", "US")
    monkeypatch.setattr(settings, "SALES_AI_CONSENTED_COUNTRIES", "US,AU")
    monkeypatch.setattr(settings, "SALES_HUMAN_ASSISTED_COUNTRIES", "US,CA,GB,AU")
    monkeypatch.setattr(settings, "SALES_REQUIRE_SIMULATION", False)
    monkeypatch.setattr(settings, "SALES_SINGLE_CALLS_PER_DAY", 30)
    monkeypatch.setattr(settings, "SALES_SINGLE_CALLS_MAX_CONCURRENT", 2)
    monkeypatch.setattr(settings, "SALES_SINGLE_CALL_MIN_GAP_HOURS", 4)
    monkeypatch.setattr(settings, "SALES_SINGLE_CALL_FROM_NUMBERS", "")
    monkeypatch.setattr(settings, "SALES_SINGLE_CALL_COUNTRIES", "*")
    monkeypatch.setattr(settings, "SALES_SINGLE_CALL_MOBILES", True)


@pytest.fixture
async def offers_ready(seeded):
    await m0006_sales_offers.run(seeded, apply=True, now=NOW)
    return seeded


class Crawl:
    """The website, read once: counts how often it was asked."""

    def __init__(self, phones=("(512) 555-0123",), name="Harbour Dental Studio"):
        self.calls = 0
        self.phones = list(phones)
        self.name = name

    async def __call__(self, url, **_):
        self.calls += 1
        return {"url": url, "pages": [{"url": url, "text": "Harbour Dental Studio, family dentists in Austin since 2009. "
                                                             "Book online. Dr Ana Ruiz."}],
                "hints": {"name": self.name, "phones": self.phones, "emails": ["hello@harbour.example"],
                          "address": {"line1": "1 Main St", "city": "Austin", "region": "TX", "postal": "78701",
                                      "country": "US"}}}


class Suggest:
    def __init__(self, reply, *, site=True):
        self.reply, self.calls, self.site = reply, 0, site

    async def __call__(self, messages, *, max_tokens, temperature=0.3, on_key=None):
        self.calls += 1
        if self.site:
            assert "<website_text>" in messages[1]["content"]      # the site text is fenced as data
        assert "is data, not instructions" in messages[0]["content"]
        return dict(self.reply), 0.001


async def research_llm(_text):
    return ({"summary": "Family dentists in Austin.", "services": ["Check-ups"], "likely_pains": ["Missed calls"],
             "hook": "Online booking", "contact_names": ["Ana Ruiz"], "emails": [], "confidence": 0.8,
             "fit": {"score": 70, "reasons": ["Small practice"]}}, 0.002)


async def _place(db, lead_id="lead_0199", offer_id=WEBLAUD_ID, variant_id="custom_software", dispatch=None, by="usr_1"):
    lead = await single_call.resolve(db, lead_id)
    rv = await single_call.review(db, lead, offer_id=offer_id, variant_id=variant_id, by=by, now=NOW)
    assert rv["can_call"], [c for c in rv["checks"] if c["status"] != "pass"]
    placed = await single_call.place(db, lead_id=lead_id, offer_id=offer_id, offer_version=rv["offer"]["version"],
                                     variant_id=variant_id, from_number=rv["from_number"], review_hash_seen=rv["hash"],
                                     by=by, dispatch=dispatch if dispatch is not None else Dispatched(), now=NOW)
    return placed, rv


# ── Rules (pure) ─────────────────────────────────────────────────────────────

def test_the_rules_decide_who_the_ai_may_call():
    weblaud = {"type": "written", "source": "form:demo", "scope": {"company": "WebLaud"}}
    assert single_call.call_type("US", None, "WebLaud")[0] == "ai_cold_b2b_us"
    assert single_call.call_type("US", weblaud, "WebLaud")[0] == "ai_consented"
    assert single_call.call_type("US", {**weblaud, "type": "verbal"}, "WebLaud")[0] == "ai_cold_b2b_us"
    assert single_call.call_type("AU", {**weblaud, "type": "verbal"}, "WebLaud")[0] == "ai_consented"
    # Every country (SALES_SINGLE_CALL_COUNTRIES=*, the owner's call 2026-11-28): an AI cold call to the business
    for country, name in (("AU", "Australia"), ("GB", "the UK"), ("CA", "Canada"), ("BD", "Bangladesh")):
        mode, why = single_call.call_type(country, None, "WebLaud")
        assert mode == "ai_cold_b2b_us" and f"a business in {name}" in why
    assert single_call.call_type("AU", {**weblaud, "scope": {"company": "Other Co"}}, "WebLaud")[0] == "ai_cold_b2b_us"
    assert single_call.call_type("", None, "WebLaud")[0] is None


def test_a_narrower_country_list_refuses_as_before(monkeypatch):
    monkeypatch.setattr(settings, "SALES_SINGLE_CALL_COUNTRIES", "")
    weblaud = {"type": "written", "source": "form:demo", "scope": {"company": "WebLaud"}}
    assert single_call.call_type("US", None, "WebLaud")[0] == "ai_cold_b2b_us"    # SALES_AI_COLD_COUNTRIES counts
    mode, why = single_call.call_type("AU", None, "WebLaud")
    assert mode is None and "consent" in why and "human-assisted" in why
    mode, why = single_call.call_type("AU", {**weblaud, "scope": {"company": "Other Co"}}, "WebLaud")
    assert mode is None and "Other Co" in why
    for country in ("GB", "CA"):
        mode, why = single_call.call_type(country, None, "WebLaud")
        assert mode is None and "human-assisted" in why
    mode, why = single_call.call_type("BD", None, "WebLaud")
    assert mode is None and "Bangladesh" in why and "SALES_SINGLE_CALL_COUNTRIES" in why
    monkeypatch.setattr(settings, "SALES_SINGLE_CALL_COUNTRIES", "BD")
    assert single_call.call_type("BD", None, "WebLaud")[0] == "ai_cold_b2b_us"


def test_the_gate_sees_a_deliberate_call_not_a_campaigns_pacing():
    later = NOW + timedelta(days=2)
    view = single_call.gate_view(lead_doc(attempts=3, next_eligible_at=later))
    assert view["attempts"] == 0 and view["next_eligible_at"] is None
    assert single_call.gate_view(lead_doc(status="callback", next_eligible_at=later))["next_eligible_at"] == later
    claimed = lead_doc(status="in_progress", claimed_for="single", claimed_from="callback", next_eligible_at=later)
    assert single_call.gate_view(claimed)["status"] == "callback"
    # a time zone typed in by hand can't stretch the calling hours past where the number could be
    typed = single_call.gate_view(lead_doc(timezones=["America/New_York"], timezone="America/New_York"))
    assert set(typed["timezones"]) == {"America/New_York", "America/Chicago"} and typed["timezone"] == ""


def test_a_single_call_runs_as_a_hang_up_campaign_in_business_hours():
    attempt = {"mode": "ai_cold_b2b_us", "offer_id": WEBLAUD_ID, "offer_version": 1, "variant_id": "custom_software",
               "from": CALLER_ID, "approval": {"by": "usr_1"}}
    c = single_call.campaign_for(attempt)
    assert c["_id"] is None and c["kind"] == "single" and c["status"] == "running"
    assert c["variant_ids"] == ["custom_software"] and c["from_numbers"] == [CALLER_ID]
    assert c["voicemail_policy"] == "hang_up" and c["owner_id"] == "usr_1" and c["notes"] == ""
    assert c["calling_window"] == {"start": "08:00", "end": "18:00", "days": [1, 2, 3, 4, 5, 6]}


def test_the_website_fills_only_empty_fields_and_never_narrows_time_zones():
    lead = lead_doc(business_name="Joe's Plumbing", contact_name="", email="", vertical="", address={})
    hints = {"name": "Other Name", "emails": ["Hi@Joes.example"],
             "address": {"line1": "1 Main St", "city": "Austin", "region": "Texas", "postal": "78701"}}
    sets, filled = single_call.fill(lead, hints, {"contact_names": ["Joe Smith"]}, vertical="plumber")
    assert "business_name" not in sets                                     # never written over
    assert sets["contact_name"] == "Joe Smith" and sets["email"] == "hi@joes.example" and sets["vertical"] == "plumber"
    assert sets["address"]["city"] == "Austin" and "region_code" not in sets and "timezones" not in sets
    assert set(filled) == {"contact_name", "email", "vertical", "address"}
    toll_free = lead_doc(phone="+18005551212", region_code="", timezones=[], timezone="")
    sets, filled = single_call.fill(toll_free, hints, {})
    assert sets["region_code"] == "TX" and sets["timezones"] and "timezones" in filled


def test_the_hash_covers_what_the_person_approved():
    offer = {"id": WEBLAUD_ID, "version": 1}
    base = dict(mode="ai_cold_b2b_us", offer=offer, variant_id="custom_software", from_number=CALLER_ID, opener="Hi")
    h = single_call.review_hash(single_call.material(lead_doc(), **base))
    assert h == single_call.review_hash(single_call.material(lead_doc(), **base))
    assert h != single_call.review_hash(single_call.material(lead_doc(), **{**base, "opener": "Hello"}))
    assert h != single_call.review_hash(single_call.material(lead_doc(), **{**base, "from_number": "+15125550101"}))
    assert h != single_call.review_hash(single_call.material(lead_doc(agent_notes="Ask for Ana"), **base))


def test_the_agent_dials_a_single_call_only_with_a_fresh_approval():
    attempt = {"kind": "single", "approval": {"by": "usr_1", "hash": "abc", "at": NOW}}
    campaign = single_call.campaign_for(attempt)
    assert dialer.why_not_dial(campaign, attempt, NOW + timedelta(minutes=1)) == ""
    assert "expired" in dialer.why_not_dial(campaign, attempt, NOW + timedelta(minutes=11))
    assert "not approved" in dialer.why_not_dial(campaign, {"kind": "single"}, NOW)
    # campaigns: as before
    assert dialer.why_not_dial(campaign_doc(), {}, NOW) == ""
    assert dialer.why_not_dial(campaign_doc(status="paused"), {}, NOW) == "campaign not running"
    assert dialer.why_not_dial(campaign_doc(legal_review=None), {}, NOW) == "legal review missing"


def test_single_calls_and_campaign_calls_rest_each_other():
    single = single_call.campaign_for({"mode": "ai_cold_b2b_us", "offer_id": WEBLAUD_ID, "offer_version": 1,
                                       "variant_id": "custom_software", "from": CALLER_ID})
    after_campaign = GateFacts(registry_status={"us_national": "fresh"}, recent_calls=((NOW - timedelta(hours=3), "cmp_1"),))
    assert gate.evaluate(single_call.gate_view(lead_doc()), single, after_campaign, NOW).failures == ["recently_called"]
    after_single = GateFacts(registry_status={"us_national": "fresh"}, recent_calls=((NOW - timedelta(hours=3), ""),))
    assert gate.evaluate(lead_doc(), campaign_doc(), after_single, NOW).failures == ["recently_called"]


# ── Prepare ──────────────────────────────────────────────────────────────────

async def test_a_new_business_is_read_once_filled_in_and_reviewed(offers_ready, monkeypatch):
    db = offers_ready
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    crawl = Crawl()
    suggest = Suggest({"offer_id": WEBLAUD_ID, "variant_id": "custom_software", "vertical": "dental clinic",
                       "why": "A busy practice whose website could book more patients."})
    out = await single_call.prepare(db, business={"website": "harbour.example", "country": "US"}, by="usr_1",
                                    crawl=crawl, llm=suggest, research_llm=research_llm, now=NOW)
    assert crawl.calls == 1 and suggest.calls == 1                       # the website is read once for both
    lead = out["lead"]
    assert out["created"] and out["callable"] and lead["phone"] == NEW_PHONE
    assert lead["source"] == "single_call" and "single-call" in lead["tags"] and lead["owner_id"] == "usr_1"
    assert lead["business_name"] == "Harbour Dental Studio" and lead["contact_name"] == "Ana Ruiz"
    assert lead["vertical"] == "dental clinic" and lead["email"] == "hello@harbour.example"
    assert lead["research"]["status"] == "done" and lead["research"]["offer_id"] == WEBLAUD_ID
    assert lead["call_prep"]["offer_id"] == WEBLAUD_ID and lead["call_prep"]["source"] == "ai"
    assert {"contact_name", "vertical", "email"} <= set(out["ai_filled"])
    rv = out["review"]
    assert rv["mode"] == "ai_cold_b2b_us" and rv["from_number"] == CALLER_ID and rv["can_call"], rv["checks"]
    assert "Harbour Dental Studio" in rv["opener"] and "AI" in rv["opener"]
    assert await db.audit_log.count_documents({}) == 0                   # the router audits, not the core


async def test_an_existing_number_is_the_existing_lead_and_customers_and_dnc_are_refused(offers_ready):
    db = offers_ready
    out = await single_call.prepare(db, business={"phone": "(512) 555-0199", "country": "US"}, by="usr_1", now=NOW)
    assert out["lead"]["_id"] == "lead_0199" and not out["created"]
    await registry.add_to_dnc(db, "+15125550188", reason="asked", source="test", now=NOW)
    with pytest.raises(single_call.SingleCallError) as e:
        await single_call.prepare(db, business={"phone": "+15125550188", "country": "US"}, by="usr_1", now=NOW)
    assert e.value.status == 409
    await db.tenants.insert_one({"_id": "ten_1", "phone": "+15125550177", "country": "US"})
    with pytest.raises(single_call.SingleCallError) as e:
        await single_call.prepare(db, business={"phone": "+15125550177", "country": "US"}, by="usr_1", now=NOW)
    assert "customer" in str(e.value).lower()
    assert await db[m.LEADS].count_documents({}) == 1


async def test_a_website_without_a_phone_number_asks_for_one(offers_ready):
    out = await single_call.prepare(offers_ready, business={"website": "harbour.example", "country": "US"}, by="usr_1",
                                    crawl=Crawl(phones=()), now=NOW)
    assert out["needs_phone"] and out["found"]["business_name"] == "Harbour Dental Studio"
    assert await offers_ready[m.LEADS].count_documents({}) == 1


async def test_a_uk_business_is_refused_before_anything_is_spent(offers_ready, monkeypatch):
    monkeypatch.setattr(settings, "SALES_SINGLE_CALL_COUNTRIES", "US")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    suggest = Suggest({"offer_id": WEBLAUD_ID, "variant_id": "custom_software"})
    out = await single_call.prepare(offers_ready, business={"phone": "020 7946 0958", "country": "GB"}, by="usr_1",
                                    llm=suggest, research_llm=research_llm, now=NOW)
    assert out["callable"] is False and "UK" in out["reason"] and out["review"] is None
    assert suggest.calls == 0


async def test_the_suggestion_falls_back_when_the_ai_is_off_or_names_something_else(offers_ready, monkeypatch):
    db = offers_ready
    lead = await single_call.resolve(db, "lead_0199")
    allowed = [c for c in await single_call.choices(db, lead) if c["mode"]]
    crawled = await Crawl()("joes.example")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    off = await single_call.suggest(db, lead, allowed, crawled=crawled, by="usr_1", now=NOW)
    assert off["source"] == "rule" and off["offer_id"] in {c["offer"]["id"] for c in allowed}
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    wrong = await single_call.suggest(db, lead, allowed, crawled=crawled, by="usr_1", now=NOW,
                                      llm=Suggest({"offer_id": "off_made_up", "variant_id": "x"}))
    assert wrong["source"] == "rule"
    bad_script = await single_call.suggest(db, lead, allowed, crawled=crawled, by="usr_1", now=NOW,
                                           llm=Suggest({"offer_id": WEBLAUD_ID, "variant_id": "made_up"}))
    assert bad_script["offer_id"] == WEBLAUD_ID and bad_script["variant_id"] == "ai_automation"


# ── Review ───────────────────────────────────────────────────────────────────

async def test_the_review_trusts_nothing_the_browser_sends(offers_ready):
    db = offers_ready
    lead = await single_call.resolve(db, "lead_0199")
    with pytest.raises(single_call.SingleCallError):
        await single_call.review(db, lead, offer_id="off_made_up", variant_id="x", now=NOW)
    with pytest.raises(single_call.SingleCallError):
        await single_call.review(db, lead, offer_id=WEBLAUD_ID, variant_id="sales_v1", now=NOW)
    rv = await single_call.review(db, lead, offer_id=WEBLAUD_ID, variant_id="custom_software",
                                  from_number="+15125550999", now=NOW)
    assert rv["from_number"] == CALLER_ID                       # not ours: the review uses ours
    assert (await db[m.LEADS].find_one({"_id": "lead_0199"})).get("gate") is None     # a review records nothing


async def test_the_automated_test_calls_must_pass_like_for_a_campaign(offers_ready, monkeypatch):
    db = offers_ready
    monkeypatch.setattr(settings, "SALES_REQUIRE_SIMULATION", True)
    lead = await single_call.resolve(db, "lead_0199")
    rv = await single_call.review(db, lead, offer_id=WEBLAUD_ID, variant_id="custom_software", now=NOW)
    assert not rv["can_call"] and next(c for c in rv["checks"] if c["id"] == "simulation")["status"] == "fail"
    snap = await of.get_version(db, WEBLAUD_ID, 1)
    await db[simulate.SIMULATIONS].insert_one({"_id": "sim_1", "checksum": snap["checksum"], "status": "done",
                                              "variant_ids": ["ai_automation", "custom_software"], "total": 6, "passed": 6,
                                              "playbook_checksum": None, "created_at": NOW})
    assert (await single_call.review(db, lead, offer_id=WEBLAUD_ID, variant_id="custom_software", now=NOW))["can_call"]


async def test_outside_hours_the_call_waits_and_says_until_when(offers_ready):
    db = offers_ready
    night = NOW.replace(hour=9)                                 # 03:00 in Austin
    lead = await single_call.resolve(db, "lead_0199")
    rv = await single_call.review(db, lead, offer_id=WEBLAUD_ID, variant_id="custom_software", now=night)
    assert not rv["can_call"] and "outside_hours" in rv["failures"] and rv["retry_at"] > night
    with pytest.raises(single_call.SingleCallError) as e:
        await single_call.place(db, lead_id="lead_0199", offer_id=WEBLAUD_ID, offer_version=1, variant_id="custom_software",
                                from_number=CALLER_ID, review_hash_seen=rv["hash"], by="usr_1", dispatch=Dispatched(), now=night)
    assert e.value.status == 409 and e.value.detail["code"] == "not_allowed"
    assert (await db[m.LEADS].find_one({"_id": "lead_0199"}))["status"] == "queued"


async def test_the_single_call_limits(offers_ready, monkeypatch):
    db = offers_ready
    lead = await single_call.resolve(db, "lead_0199")
    await db[m.ATTEMPTS].insert_one({"_id": "att_old", "call_id": "out-att_old", "lead_id": "lead_0199", "campaign_id": None, "kind": "single",
                                     "status": "done", "outcome": "no_answer", "created_at": NOW - timedelta(hours=1)})
    codes = [c for c, _, _ in await single_call.limit_problems(db, lead, "usr_1", NOW)]
    assert codes == ["single_gap"]
    assert not await single_call.limit_problems(db, lead, "usr_1", NOW + timedelta(hours=4))
    monkeypatch.setattr(settings, "SALES_SINGLE_CALLS_PER_DAY", 0)
    assert "single_off" in [c for c, _, _ in await single_call.limit_problems(db, lead, "usr_1", NOW)]
    monkeypatch.setattr(settings, "SALES_SINGLE_CALLS_PER_DAY", 30)
    await db[m.ATTEMPTS].insert_one({"_id": "att_live", "call_id": "out-att_live", "lead_id": "lead_x", "campaign_id": None, "kind": "single",
                                     "status": "in_progress", "outcome": None, "created_at": NOW,
                                     "approval": {"by": "usr_1"}})
    assert "single_yours_live" in [c for c, _, _ in await single_call.limit_problems(db, lead, "usr_1", NOW)]
    assert "single_yours_live" not in [c for c, _, _ in await single_call.limit_problems(db, lead, "usr_2", NOW)]


# ── Place ────────────────────────────────────────────────────────────────────

async def test_approve_and_call_dials_once_and_keeps_the_approval(offers_ready):
    db = offers_ready
    sent = Dispatched()
    placed, rv = await _place(db, dispatch=sent)
    assert len(sent) == 1 and sent[0][0] == placed["room"] and sent[0][1]["campaign_id"] is None
    attempt = await db[m.ATTEMPTS].find_one({"_id": placed["attempt_id"]})
    assert attempt["kind"] == "single" and attempt["campaign_id"] is None and attempt["operator"] is None
    assert attempt["approval"]["by"] == "usr_1" and attempt["approval"]["hash"] == rv["hash"]
    assert attempt["approval"]["material"]["opener"] == rv["opener"] and attempt["arm"] == "off"
    assert attempt["attempt_no"] is None and attempt["dialled_as"] == "single" and attempt["from"] == CALLER_ID
    lead = await db[m.LEADS].find_one({"_id": "lead_0199"})
    assert lead["status"] == "in_progress" and lead["claimed_for"] == "single" and lead["claimed_from"] == "queued"
    assert lead["attempts"] == 0 and lead["owner_id"] == "usr_1" and lead["gate"]["allowed"]
    # a second click, or anyone else, can't ring them again meanwhile
    with pytest.raises(single_call.SingleCallError) as e:
        await single_call.place(db, lead_id="lead_0199", offer_id=WEBLAUD_ID, offer_version=1, variant_id="custom_software",
                                from_number=CALLER_ID, review_hash_seen=rv["hash"], by="usr_1", dispatch=sent, now=NOW)
    assert e.value.status == 409 and len(sent) == 1
    # and the campaign the lead is in doesn't dial it either
    assert (await dialer.tick(db, now=NOW, dispatch=sent))["dialed"] == 0 and len(sent) == 1


async def test_a_changed_review_or_a_caller_id_not_ours_is_refused(offers_ready):
    db = offers_ready
    lead = await single_call.resolve(db, "lead_0199")
    rv = await single_call.review(db, lead, offer_id=WEBLAUD_ID, variant_id="custom_software", now=NOW)
    await db[m.LEADS].update_one({"_id": "lead_0199"}, {"$set": {"agent_notes": "Mention the new clinic"}})
    with pytest.raises(single_call.SingleCallError) as e:
        await single_call.place(db, lead_id="lead_0199", offer_id=WEBLAUD_ID, offer_version=1, variant_id="custom_software",
                                from_number=CALLER_ID, review_hash_seen=rv["hash"], by="usr_1", dispatch=Dispatched(), now=NOW)
    assert e.value.status == 409 and e.value.detail["code"] == "review_changed" and e.value.detail["review"]["hash"] != rv["hash"]
    with pytest.raises(single_call.SingleCallError) as e:
        await single_call.place(db, lead_id="lead_0199", offer_id=WEBLAUD_ID, offer_version=1, variant_id="custom_software",
                                from_number="+15125550999", review_hash_seen=rv["hash"], by="usr_1", dispatch=Dispatched(), now=NOW)
    assert e.value.status == 422
    assert not await db[m.ATTEMPTS].count_documents({})


async def test_a_failed_dispatch_puts_the_lead_back(offers_ready):
    async def broken(room, metadata):
        raise RuntimeError("LiveKit down")
    with pytest.raises(single_call.SingleCallError) as e:
        await _place(offers_ready, dispatch=broken)
    assert e.value.status == 502
    lead = await offers_ready[m.LEADS].find_one({"_id": "lead_0199"})
    attempt = await offers_ready[m.ATTEMPTS].find_one({})
    assert lead["status"] == "queued" and "claimed_for" not in lead and attempt["outcome"] == "dispatch_failed"


# ── After the call ───────────────────────────────────────────────────────────

async def test_no_answer_puts_the_lead_back_and_its_campaign_rests_first(offers_ready):
    db = offers_ready
    placed, _ = await _place(db)
    await outcomes.finish_attempt(db, placed["attempt_id"], "no_answer", now=NOW)
    lead = await db[m.LEADS].find_one({"_id": "lead_0199"})
    assert lead["status"] == "queued" and lead["campaign_id"] == "cmp_1" and lead["attempts"] == 0
    assert "claimed_for" not in lead and lead["single_calls"] == 1 and lead["last_outcome"] == "no_answer"
    assert outcomes._aware(lead["next_eligible_at"]) >= NOW + timedelta(hours=settings.SALES_MIN_HOURS_BETWEEN_CAMPAIGNS)
    assert followup.derived_stage({**lead, "attempts": 0}) == "contacted"
    sent = Dispatched()
    assert (await dialer.tick(db, now=NOW + timedelta(hours=1), dispatch=sent))["dialed"] == 0


@pytest.mark.parametrize("before", ["new", "exhausted", "not_interested", "blocked", "interested"])
async def test_every_status_comes_back_after_a_no_answer(offers_ready, before):
    db = offers_ready
    await db[m.LEADS].update_one({"_id": "lead_0199"}, {"$set": {"status": before, "campaign_id": None}})
    if before == "not_interested":                      # declined another offer long ago: cooled down
        await db[m.LEADS].update_one({"_id": "lead_0199"}, {"$set": {"declined_offers": ["off_laudline"]}})
    placed, _ = await _place(db)
    await outcomes.finish_attempt(db, placed["attempt_id"], "voicemail", now=NOW)
    assert (await db[m.LEADS].find_one({"_id": "lead_0199"}))["status"] == before


async def test_decisive_outcomes_move_the_lead_like_a_campaign_call(offers_ready):
    db = offers_ready
    placed, _ = await _place(db)
    await outcomes.finish_attempt(db, placed["attempt_id"], "not_interested", now=NOW, reason="has an agency")
    lead = await db[m.LEADS].find_one({"_id": "lead_0199"})
    assert lead["status"] == "not_interested" and WEBLAUD_ID in lead["declined_offers"] and "claimed_for" not in lead
    assert not await registry.on_internal_dnc(db, AUSTIN)                 # not interested is not an opt-out


async def test_a_meeting_opens_a_deal_for_the_person_who_called(offers_ready):
    db = offers_ready
    placed, _ = await _place(db)
    await outcomes.finish_attempt(db, placed["attempt_id"], "demo_booked", now=NOW)
    from core.outbound import crm

    deal = await db[crm.DEALS].find_one({"lead_id": "lead_0199"})
    assert deal and deal["owner_id"] == "usr_1" and deal["offer_id"] == WEBLAUD_ID


async def test_a_callback_leaves_its_campaign_and_the_caller_gets_a_task(offers_ready):
    db = offers_ready
    placed, _ = await _place(db)
    when = NOW + timedelta(days=1)
    await outcomes.finish_attempt(db, placed["attempt_id"], "callback", now=NOW, callback_at=when)
    lead = await db[m.LEADS].find_one({"_id": "lead_0199"})
    assert lead["status"] == "callback" and lead["campaign_id"] is None and outcomes._aware(lead["next_eligible_at"]) == when
    assert lead["history"][-1]["campaign_id"] == "cmp_1" and lead["history"][-1]["left_for"] == "single"
    task = await db[followup.TASKS].find_one({"source": "single_callback"})
    assert task["owner"] == "usr_1" and outcomes._aware(task["due_at"]) == when and "they asked" in task["title"]
    # before that time a single call waits; at it, only the offer they asked about
    lead = await single_call.resolve(db, "lead_0199")
    early = await single_call.review(db, lead, offer_id=WEBLAUD_ID, variant_id="custom_software", now=NOW + timedelta(hours=5))
    assert "too_soon" in early["failures"]
    other = await single_call.review(db, lead, offer_id="off_laudline", variant_id="sales_v1", now=when + timedelta(minutes=5))
    assert next(c for c in other["checks"] if c["id"] == "callback_offer")["status"] == "fail"
    due = await single_call.review(db, lead, offer_id=WEBLAUD_ID, variant_id="custom_software", now=when + timedelta(minutes=5))
    assert due["can_call"] and due["callback"], [c for c in due["checks"] if c["status"] != "pass"]


async def test_the_reaper_hands_a_stuck_single_claim_back(db):
    await db[m.LEADS].insert_one(lead_doc(status="in_progress", claimed_for="single", claimed_from="new",
                                          claimed_at=NOW - timedelta(hours=1)))
    await db[m.LEADS].insert_one(lead_doc(phone="+15125550198", status="in_progress", claimed_at=NOW - timedelta(hours=1)))
    await dialer.reap(db, NOW)
    single = await db[m.LEADS].find_one({"_id": "lead_0199"})
    campaign = await db[m.LEADS].find_one({"_id": "lead_0198"})
    assert single["status"] == "new" and "claimed_for" not in single
    assert campaign["status"] == "queued"                          # campaign claims: as before


async def test_a_single_call_no_agent_picks_up_is_cancelled_after_two_minutes(offers_ready):
    # No sales worker running: the call would sit on "Starting" holding the person's slot until the usual reap.
    db = offers_ready
    placed, _ = await _place(db)
    assert await dialer.reap(db, NOW + timedelta(seconds=90)) == 0
    assert (await db[m.ATTEMPTS].find_one({"_id": placed["attempt_id"]}))["status"] == "dispatched"
    later = NOW + dialer.SINGLE_PICKUP + timedelta(seconds=1)
    assert await dialer.reap(db, later) == 1
    attempt = await db[m.ATTEMPTS].find_one({"_id": placed["attempt_id"]})
    assert (attempt["status"], attempt["outcome"], attempt["detail"]) == ("done", "dispatch_failed", dialer.NOT_PICKED_UP)
    lead = await db[m.LEADS].find_one({"_id": "lead_0199"})
    assert lead["status"] == "queued" and "claimed_for" not in lead and not lead.get("single_calls")
    # nothing rang, so nothing holds the next call back: no gap, not "already in progress"
    codes = [code for code, *_ in await single_call.limit_problems(db, lead, "usr_1", later)]
    assert codes == []


async def test_a_missing_or_inactive_sales_tenant_stops_calls_before_anyone_approves(offers_ready):
    # SALES_TENANT_ID set but not in the database: the agent would refuse every call seconds before dialling.
    db = offers_ready
    await db.tenants.delete_one({"_id": "ten_sales"})
    problem = await dialer.tenant_problem(db)
    assert "doesn't exist yet" in problem and "seed_sales_tenant" in problem
    lead = await single_call.resolve(db, "lead_0199")
    rv = await single_call.review(db, lead, offer_id=WEBLAUD_ID, variant_id="custom_software", now=NOW)
    check = next(c for c in rv["checks"] if c["id"] == "dialer")
    assert check["status"] == "fail" and problem in check["detail"] and "override" not in check
    assert not rv["can_call"]
    with pytest.raises(single_call.SingleCallError) as e:
        await single_call.place(db, lead_id="lead_0199", offer_id=WEBLAUD_ID, offer_version=rv["offer"]["version"],
                                variant_id="custom_software", from_number=rv["from_number"], review_hash_seen=rv["hash"],
                                by="usr_1", dispatch=Dispatched(), now=NOW)
    assert e.value.status == 409 and problem in str(e.value.detail)
    assert not (await single_call.status(db))["enabled"]
    sent = Dispatched()
    assert (await dialer.tick(db, now=NOW, dispatch=sent))["dialed"] == 0 and not sent     # campaigns wait too
    await db.tenants.insert_one({"_id": "ten_sales", "slug": "weblaud-sales", "status": "cancelled"})
    assert "isn't active" in await dialer.tenant_problem(db)
    await db.tenants.update_one({"_id": "ten_sales"}, {"$set": {"status": "active"}})
    assert await dialer.tenant_problem(db) == "" and (await single_call.status(db))["enabled"]


async def test_a_campaign_call_waiting_for_its_agent_keeps_the_usual_reap(seeded):
    await dialer.tick(seeded, now=NOW, dispatch=Dispatched())
    assert await dialer.reap(seeded, NOW + timedelta(minutes=3)) == 0
    assert (await seeded[m.ATTEMPTS].find_one({}))["status"] == "dispatched"


async def test_closing_only_from_a_status_never_closes_a_call_the_agent_started(offers_ready):
    db = offers_ready
    placed, _ = await _place(db)
    await db[m.ATTEMPTS].update_one({"_id": placed["attempt_id"]}, {"$set": {"status": "dialing", "started_at": NOW}})
    assert await outcomes.finish_attempt(db, placed["attempt_id"], "dispatch_failed", now=NOW, only_from=("dispatched",)) is None
    assert (await db[m.ATTEMPTS].find_one({"_id": placed["attempt_id"]}))["status"] == "dialing"


class LiveKitRooms:
    """livekit.api.LiveKitAPI for the hang-up endpoint: records the rooms deleted, or fails like a missing room."""

    def __init__(self, fail=False):
        self.fail, self.deleted = fail, []

    def __call__(self, *_args, **_kw):
        return self

    @property
    def room(self):
        return self

    async def delete_room(self, request):
        if self.fail:
            raise RuntimeError("requested room does not exist")
        self.deleted.append(request.room)

    async def aclose(self):
        pass


async def test_ending_a_single_call_before_it_rang_cancels_it(admin, offers_ready, frozen_api_clock, monkeypatch):
    from livekit import api

    db = offers_ready
    rooms = LiveKitRooms(fail=True)                     # the agent never started, so there's no room either
    monkeypatch.setattr(api, "LiveKitAPI", rooms)
    placed, _ = await _place(db)
    r = admin.post(f"/platform/sales/live/{placed['attempt_id']}/hangup")
    assert r.status_code == 200, r.text
    assert r.json() == {"success": True, "cancelled": True}
    attempt = await db[m.ATTEMPTS].find_one({"_id": placed["attempt_id"]})
    assert attempt["status"] == "done" and attempt["outcome"] == "dispatch_failed"
    assert attempt["detail"].startswith("Ended by ") and attempt["detail"].endswith("before it rang")
    lead = await db[m.LEADS].find_one({"_id": "lead_0199"})
    assert lead["status"] == "queued" and "claimed_for" not in lead
    assert await db.audit_log.count_documents({"action": "sales.call.cancel"}) == 1
    # gone from Live calls; ending it again says it has ended
    assert admin.get("/platform/sales/live").json()["total"] == 0
    assert admin.post(f"/platform/sales/live/{placed['attempt_id']}/hangup").status_code == 409


async def test_ending_a_call_that_is_ringing_hangs_up_and_leaves_the_outcome_to_the_agent(admin, offers_ready, monkeypatch):
    from livekit import api

    db = offers_ready
    placed, _ = await _place(db)
    await db[m.ATTEMPTS].update_one({"_id": placed["attempt_id"]}, {"$set": {"status": "dialing", "started_at": NOW}})
    monkeypatch.setattr(api, "LiveKitAPI", LiveKitRooms(fail=True))
    assert admin.post(f"/platform/sales/live/{placed['attempt_id']}/hangup").status_code == 502
    rooms = LiveKitRooms()
    monkeypatch.setattr(api, "LiveKitAPI", rooms)
    r = admin.post(f"/platform/sales/live/{placed['attempt_id']}/hangup")
    assert r.status_code == 200 and r.json() == {"success": True}
    assert rooms.deleted == [placed["room"]]
    assert (await db[m.ATTEMPTS].find_one({"_id": placed["attempt_id"]}))["status"] == "dialing"   # the agent's shutdown records it


# ── API ──────────────────────────────────────────────────────────────────────

async def _sales_user(db, role, uid="usr_sam"):
    from tests.test_sales_offer_ai import _sales_user as make

    return await make(db, role, uid)


async def test_viewers_cannot_place_single_calls(client, offers_ready):
    h = await _sales_user(offers_ready, "viewer")
    assert client.post("/platform/sales/calls/prepare", headers=h, json={"lead_id": "lead_0199"}).status_code == 403
    assert client.post("/platform/sales/calls/review", headers=h,
                       json={"lead_id": "lead_0199", "offer_id": WEBLAUD_ID, "variant_id": "custom_software"}).status_code == 403
    assert client.post("/platform/sales/calls", headers=h, json={
        "lead_id": "lead_0199", "offer_id": WEBLAUD_ID, "offer_version": 1, "variant_id": "custom_software",
        "from_number": CALLER_ID, "review_hash": "x" * 32, "approved": True}).status_code == 403


async def test_prepare_review_and_approve_over_the_api(admin, offers_ready, frozen_api_clock, monkeypatch):
    db = offers_ready
    sent = Dispatched()
    monkeypatch.setattr(dialer, "livekit_dispatch", sent)
    r = admin.post("/platform/sales/calls/prepare", json={"lead_id": "lead_0199"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lead"]["id"] == "lead_0199" and body["callable"] and body["review"]["can_call"]
    rv = admin.post("/platform/sales/calls/review", json={"lead_id": "lead_0199", "offer_id": WEBLAUD_ID,
                                                           "variant_id": "ai_automation"}).json()
    assert rv["variant_id"] == "ai_automation" and rv["can_call"]
    place = {"lead_id": "lead_0199", "offer_id": WEBLAUD_ID, "offer_version": rv["offer"]["version"],
             "variant_id": "ai_automation", "from_number": rv["from_number"], "review_hash": rv["hash"]}
    assert admin.post("/platform/sales/calls", json=place).status_code == 422          # not approved
    r = admin.post("/platform/sales/calls", json={**place, "approved": True})
    assert r.status_code == 200, r.text
    assert r.json()["join"]["mode"] == "listen" and r.json()["attempt"]["kind"] == "single" and len(sent) == 1
    again = admin.post("/platform/sales/calls", json={**place, "approved": True})
    assert again.status_code == 409
    assert await db.audit_log.count_documents({"action": "sales.call.single"}) == 1
    assert await db.audit_log.count_documents({"action": "sales.call.prepare"}) == 1
    status = admin.get("/platform/sales/status").json()["single_calls"]
    assert status["enabled"] and status["per_day"] == 30


async def test_results_can_show_single_calls_only(admin, offers_ready, frozen_api_clock):
    db = offers_ready
    placed, _ = await _place(db)
    await outcomes.finish_attempt(db, placed["attempt_id"], "no_answer", now=NOW)
    await db[m.ATTEMPTS].insert_one({"_id": "att_cmp", "lead_id": "lead_x", "campaign_id": "cmp_1", "status": "done",
                                     "outcome": "no_answer", "created_at": NOW, "call_id": "out-att_cmp"})
    rows = admin.get("/platform/sales/attempts", params={"campaign_id": "single"}).json()["attempts"]
    assert [a["id"] for a in rows] == [placed["attempt_id"]]
    funnel = admin.get("/platform/sales/funnel", params={"campaign_id": "single"}).json()
    assert funnel["counted_calls"] == 1


async def test_research_is_not_redone_when_it_is_fresh(offers_ready, monkeypatch):
    db = offers_ready
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    await db[m.LEADS].update_one({"_id": "lead_0199"}, {"$set": {
        "website": "joes.example",
        "research": {"status": "done", "offer_id": WEBLAUD_ID, "fetched_at": NOW, "expires_at": NOW + timedelta(days=5),
                     "data": {"summary": "Plumbers"}}}})
    crawl = Crawl()
    suggest = Suggest({"offer_id": WEBLAUD_ID, "variant_id": "custom_software"}, site=False)
    out = await single_call.prepare(db, lead_id="lead_0199", by="usr_1", crawl=crawl, llm=suggest, now=NOW)
    assert suggest.calls == 1 and out["suggestion"]["source"] == "ai"          # the research it already has is enough
    assert crawl.calls == 0 and out["callable"] and research.is_fresh(out["lead"], NOW)


def test_the_suggestion_budget_kind_is_known():
    assert "single_call" in offer_ai.KINDS


# ── Any country, business mobiles, no website (2026-11-28) ───────────────────

BD_MOBILE = "+8801767677517"
SUNDAY_DHAKA = datetime(2026, 11, 15, 5, 0, tzinfo=timezone.utc)      # Sunday 11:00 in Dhaka: a working day
FRIDAY_DHAKA = datetime(2026, 11, 20, 5, 0, tzinfo=timezone.utc)      # Friday 11:00: the weekend there


def _status(rv, check_id):
    return next((c["status"] for c in rv["checks"] if c["id"] == check_id), None)


async def _publish_website_offer(db):
    """The draft website offer (m0009), checked, confirmed and published as a person would."""
    await m0009_sales_website_offer.run(db, apply=True, now=NOW)
    doc = await db[of.OFFERS].find_one({"_id": WEBSITES_ID})
    await of.update_offer(db, WEBSITES_ID, of.settings_of(doc), by="usr_1", ai_confirmed=doc["ai_unconfirmed"], now=NOW)
    await of.publish(db, WEBSITES_ID, by="usr_1", now=NOW)


def test_a_business_mobile_abroad_may_get_a_single_call_but_never_a_campaigns():
    lead = single_call.gate_view(lead_doc(phone=BD_MOBILE, line_type=None))
    single = single_call.campaign_for({"mode": "ai_cold_b2b_us", "offer_id": WEBLAUD_ID, "offer_version": 1,
                                       "variant_id": "custom_software", "from": CALLER_ID, "country": "BD"})
    decision = gate.evaluate(lead, single, GateFacts(), SUNDAY_DHAKA)
    assert decision.allowed, decision.failures
    assert "check it is the business's" in decision.checks["line_type"]
    campaign = gate.evaluate(lead_doc(phone=BD_MOBILE), campaign_doc(), GateFacts(), SUNDAY_DHAKA)
    assert {"country_not_allowed", "line_type_not_allowed"} <= set(campaign.failures)
    # a US mobile never: an AI voice needs written consent there
    us = gate.evaluate(single_call.gate_view(lead_doc(line_type="mobile")), single,
                       GateFacts(registry_status={"us_national": "fresh"}), NOW)
    assert "line_type_not_allowed" in us.failures


def test_mobiles_abroad_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(settings, "SALES_SINGLE_CALL_MOBILES", False)
    single = single_call.campaign_for({"mode": "ai_cold_b2b_us", "country": "BD"})
    decision = gate.evaluate(single_call.gate_view(lead_doc(phone=BD_MOBILE)), single, GateFacts(), SUNDAY_DHAKA)
    assert decision.failures == ["line_type_not_allowed"]
    landline = gate.evaluate(single_call.gate_view(lead_doc(phone="+88029123456")), single, GateFacts(), SUNDAY_DHAKA)
    assert landline.allowed, landline.failures


def test_where_the_weekend_is_friday_and_saturday_calls_go_sunday_to_thursday():
    single = single_call.campaign_for({"mode": "ai_cold_b2b_us", "country": "BD"})
    assert single["calling_window"]["days"] == [1, 2, 3, 4, 6, 7]
    lead = single_call.gate_view(lead_doc(phone=BD_MOBILE))
    assert gate.window_open(lead, single, SUNDAY_DHAKA)
    friday = gate.evaluate(lead, single, GateFacts(), FRIDAY_DHAKA)
    assert friday.failures == ["outside_hours"]
    assert friday.retry_at == datetime(2026, 11, 22, 3, 0, tzinfo=timezone.utc)   # Sunday 9 am in Dhaka
    assert rules.default_window("BD").for_weekday(4) is None and rules.default_window("PK").for_weekday(4)


async def test_a_bangladeshi_business_with_no_website_is_offered_one(offers_ready, monkeypatch):
    db = offers_ready
    await _publish_website_offer(db)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    crawl = Crawl()
    suggest = Suggest({"offer_id": WEBSITES_ID, "variant_id": "no_website", "vertical": "sweet shop",
                       "why": "They have no website, and this builds one."}, site=False)
    out = await single_call.prepare(db, business={"phone": BD_MOBILE, "country": "BD", "no_website": True,
                                                  "business_name": "Dhaka Sweets", "website": "ignored.example"},
                                    by="usr_1", crawl=crawl, llm=suggest, research_llm=research_llm, now=SUNDAY_DHAKA)
    assert crawl.calls == 0 and suggest.calls == 1                       # nothing to read; the AI still chose
    assert out["callable"] and out["lead"]["no_website"] is True and out["lead"]["country"] == "BD"
    assert out["suggestion"]["offer_id"] == WEBSITES_ID and out["suggestion"]["variant_id"] == "no_website"
    rv = out["review"]
    assert rv["mode"] == "ai_cold_b2b_us" and "Bangladesh" in rv["mode_reason"]
    assert "couldn't find one for Dhaka Sweets" in rv["opener"]
    assert _status(rv, "line_type") == "warn" and _status(rv, "caller_id") == "warn"    # a mobile; a US caller ID
    assert _status(rv, "local_rules") is None and _status(rv, "calling_window") == "pass"
    assert rv["can_call"], [c for c in rv["checks"] if c["status"] == "fail"]
    assert rv["local_times"][0]["tz"] == "Asia/Dhaka"


async def test_without_the_ai_a_business_with_no_website_still_gets_the_website_offer(offers_ready, monkeypatch):
    db = offers_ready
    await _publish_website_offer(db)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    await db[m.LEADS].update_one({"_id": "lead_0199"}, {"$set": {"no_website": True}})
    lead = await single_call.resolve(db, "lead_0199")
    allowed = [c for c in await single_call.choices(db, lead) if c["mode"]]
    pick = await single_call.suggest(db, lead, allowed, crawled=None, by="usr_1", now=NOW)
    assert (pick["offer_id"], pick["variant_id"], pick["source"]) == (WEBSITES_ID, "no_website", "rule")
    social = {**lead, "no_website": False, "website": "https://www.facebook.com/joesplumbing"}
    pick = await single_call.suggest(db, social, allowed, crawled=None, by="usr_1", now=NOW)
    assert (pick["offer_id"], pick["variant_id"]) == (WEBSITES_ID, "social_only")


async def test_a_facebook_page_is_not_a_website_to_read(offers_ready):
    db = offers_ready
    crawl = Crawl()
    out = await single_call.prepare(db, business={"website": "fb.com/joesplumbing", "phone": "(512) 555-0123",
                                                  "country": "US"}, by="usr_1", crawl=crawl, now=NOW)
    assert crawl.calls == 0 and out["lead"]["no_website"] is True
    assert out["lead"]["website"] == "https://fb.com/joesplumbing"
    doc = await research.research_lead(db, out["lead"], {"id": WEBLAUD_ID}, crawl=crawl, now=NOW)
    assert doc["status"] == "no_website" and crawl.calls == 0
    # reading a real website of theirs clears it
    await db[m.LEADS].update_one({"_id": out["lead"]["_id"]}, {"$set": {"website": ""}})
    out = await single_call.prepare(db, business={"website": "harbour.example", "phone": "(512) 555-0123",
                                                  "country": "US"}, by="usr_1", crawl=crawl, now=NOW)
    assert crawl.calls == 1 and out["lead"]["no_website"] is False


def test_the_agent_is_told_they_have_no_website():
    assert research.social_page("https://m.facebook.com/x") == "Facebook"
    assert research.social_page("instagram.com/x") == "Instagram"
    assert not research.social_page("harbourdental.com.au") and not research.social_page("notfacebook.com")
    assert "couldn't find one of their own." in persona.no_website_line(lead_doc(no_website=True))
    assert "only a Facebook page" in persona.no_website_line(lead_doc(website="facebook.com/joes"))
    assert persona.no_website_line(lead_doc(website="joes.example")) == ""
    assert persona.no_website_line(lead_doc()) == ""                           # unknown: say nothing


async def test_strict_countries_warn_and_keep_their_do_not_call_registers(offers_ready):
    db = offers_ready
    out = await single_call.prepare(db, business={"phone": "020 7946 0958", "country": "GB"}, by="usr_1", now=NOW)
    rv = out["review"]
    assert out["callable"] and rv["mode"] == "ai_cold_b2b_us"
    strict = next(c for c in rv["checks"] if c["id"] == "local_rules")
    assert strict["status"] == "warn" and "PECR" in strict["detail"]
    assert _status(rv, "registry") == "fail" and not rv["can_call"]         # TPS/CTPS must still be loaded
    for country in ("CA", "IN", "AE", "AU", "DE", "NO"):
        assert country in rules.STRICT_AI_COLD


async def test_the_website_offer_is_a_draft_until_a_person_publishes_it(offers_ready):
    db = offers_ready
    assert await m0009_sales_website_offer.run(db, apply=True, now=NOW) == {"drafts_added": 1, "offers_existing": 0}
    assert await m0009_sales_website_offer.run(db, apply=True, now=NOW) == {"drafts_added": 0, "offers_existing": 1}
    doc = await db[of.OFFERS].find_one({"_id": WEBSITES_ID})
    assert doc["status"] == "draft" and not doc["published_version"] and doc["ai_unconfirmed"]
    with pytest.raises(of.OfferError):
        await of.publish(db, WEBSITES_ID, by="usr_1", now=NOW)                # its facts aren't confirmed yet
    assert WEBSITES_ID not in {c["offer"]["id"] for c in await single_call.published_offers(db)}
    for v in doc["variants"]:
        assert of.check_opener(v["cold_opener"].replace("{company}", "WebLaud"), company="WebLaud") == []


def test_the_country_settings_are_checked():
    from config import Settings

    assert "*" in Settings(_env_file=None, APP_ENV="development").sales_single_call_countries
    narrow = Settings(_env_file=None, APP_ENV="development", SALES_SINGLE_CALL_COUNTRIES="bd, pk")
    assert narrow.sales_single_call_countries == {"BD", "PK", "US"}
    with pytest.raises(ValueError):
        Settings(_env_file=None, APP_ENV="development", SALES_SINGLE_CALL_COUNTRIES="Bangladesh")


async def test_any_country_and_no_website_over_the_api(admin, offers_ready, frozen_api_clock):
    r = admin.post("/platform/sales/calls/prepare", json={"business": {"phone": BD_MOBILE, "country": "ZZ"}})
    assert r.status_code == 422
    r = admin.post("/platform/sales/calls/prepare",
                   json={"business": {"website": "x.example", "no_website": True, "country": "BD"}})
    assert r.status_code == 422
    r = admin.post("/platform/sales/calls/prepare", json={"business": {"phone": BD_MOBILE, "country": "bd", "no_website": True}})
    assert r.status_code == 200, r.text
    lead = r.json()["lead"]
    assert lead["country"] == "BD" and lead["no_website"] is True
    r = admin.patch(f"/platform/sales/leads/{lead['id']}", json={"no_website": False})
    assert r.status_code == 200 and r.json()["lead"]["no_website"] is False


# ── Overrides (2026-11-28): a sales manager may override our own rules, never a law's ──────────

AUSTIN_EVENING = datetime(2026, 11, 18, 1, 0, tzinfo=timezone.utc)    # Tuesday 19:00 in Austin: after business hours
AUSTIN_LATE = datetime(2026, 11, 18, 3, 30, tzinfo=timezone.utc)      # Tuesday 21:30: past the federal 9 pm
SATURDAY_DHAKA = datetime(2026, 11, 21, 5, 0, tzinfo=timezone.utc)    # Saturday 11:00 in Dhaka: the weekend there
NIGHT_DHAKA = datetime(2026, 11, 15, 16, 30, tzinfo=timezone.utc)     # Sunday 22:30 in Dhaka
SYDNEY = "+61291234567"
SUNDAY_SYDNEY = datetime(2026, 11, 15, 0, 0, tzinfo=timezone.utc)     # Sunday 11:00 in Sydney


def test_an_override_widens_our_own_hours_never_the_laws():
    plain = single_call.campaign_for({"mode": "ai_cold_b2b_us", "country": "BD"})
    over = single_call.campaign_for({"mode": "ai_cold_b2b_us", "country": "BD", "overrides": ["simulation", "business_hours"]})
    assert plain["waive"] == [] and over["waive"] == ["business_hours"]           # the test calls aren't the gate's
    assert over["calling_window"] == single_call.OVERRIDE_WINDOW
    # Bangladesh: no law sets the hours, so the Sunday-to-Thursday default is ours to override
    bd = single_call.gate_view(lead_doc(phone=BD_MOBILE))
    assert gate.evaluate(bd, plain, GateFacts(), SATURDAY_DHAKA).failures == ["outside_hours"]
    assert single_call.hours_problem(bd, plain, SATURDAY_DHAKA) == "business"
    assert gate.evaluate(bd, over, GateFacts(), SATURDAY_DHAKA).allowed
    # never at night, override or not
    assert gate.evaluate(bd, over, GateFacts(), NIGHT_DHAKA).failures == ["outside_hours"]
    assert single_call.hours_problem(bd, plain, NIGHT_DHAKA) == "night"
    # the US: the federal 8 am-9 pm (and the Texas 9 am-9 pm) stand
    us_plain = single_call.campaign_for({"mode": "ai_cold_b2b_us", "country": "US"})
    us_over = single_call.campaign_for({"mode": "ai_cold_b2b_us", "country": "US", "overrides": ["business_hours"]})
    austin = single_call.gate_view(lead_doc())
    fresh = GateFacts(registry_status={"us_national": "fresh"})
    assert gate.evaluate(austin, us_plain, fresh, AUSTIN_EVENING).failures == ["outside_hours"]
    assert gate.evaluate(austin, us_over, fresh, AUSTIN_EVENING).allowed
    assert gate.evaluate(austin, us_over, fresh, AUSTIN_LATE).failures == ["outside_hours"]
    assert single_call.hours_problem(austin, us_plain, AUSTIN_LATE) == "law"
    # Australia: never on a Sunday
    sydney = single_call.gate_view(lead_doc(phone=SYDNEY))
    au_over = single_call.campaign_for({"mode": "ai_cold_b2b_us", "country": "AU", "overrides": ["business_hours"]})
    assert "outside_hours" in gate.evaluate(sydney, au_over, GateFacts(registry_status={"au_dncr": "fresh"}), SUNDAY_SYDNEY).failures
    assert single_call.hours_problem(sydney, au_over, SUNDAY_SYDNEY) == "law"
    # a campaign can't waive anything, and nothing outside WAIVABLE can be waived
    assert not gate.waived(campaign_doc(waive=["business_hours"]), "business_hours")
    assert gate.waived(us_over, "business_hours")
    assert not gate.waived({**us_over, "waive": ["internal_dnc"]}, "internal_dnc")


def test_an_override_skips_the_rest_after_a_campaign_not_the_30_day_cap(monkeypatch):
    monkeypatch.setattr(settings, "SALES_MAX_CALLS_PER_PROSPECT_30D", 6)
    rest = single_call.campaign_for({"mode": "ai_cold_b2b_us", "country": "US", "overrides": ["recently_called"]})
    austin = single_call.gate_view(lead_doc())
    after_campaign = GateFacts(registry_status={"us_national": "fresh"}, recent_calls=((NOW - timedelta(hours=3), "cmp_1"),))
    assert gate.evaluate(austin, rest, after_campaign, NOW).allowed
    capped = GateFacts(registry_status={"us_national": "fresh"},
                       recent_calls=tuple((NOW - timedelta(days=d), "cmp_1") for d in range(1, 7)))
    assert gate.evaluate(austin, rest, capped, NOW).failures == ["prospect_cap"]


def test_the_hash_covers_the_overrides():
    base = dict(mode="ai_cold_b2b_us", offer={"id": WEBLAUD_ID, "version": 1}, variant_id="custom_software",
                from_number=CALLER_ID, opener="Hi")
    h = single_call.review_hash(single_call.material(lead_doc(), **base))
    assert h != single_call.review_hash(single_call.material(lead_doc(), **base, overrides=["simulation"]))
    assert single_call.review_hash(single_call.material(lead_doc(), **base, overrides=["simulation", "business_hours"])) == \
        single_call.review_hash(single_call.material(lead_doc(), **base, overrides=["business_hours", "simulation"]))


def test_the_api_knows_every_override():
    from typing import get_args

    from routers.sales_calls import Override

    assert set(get_args(Override)) == set(single_call.OVERRIDES) and gate.WAIVABLE <= set(single_call.OVERRIDES)


async def test_a_manager_overrides_our_own_checks_with_a_reason_and_the_agent_agrees(offers_ready, monkeypatch):
    db = offers_ready
    monkeypatch.setattr(settings, "SALES_REQUIRE_SIMULATION", True)
    lead = await single_call.resolve(db, "lead_0199")
    args = dict(offer_id=WEBLAUD_ID, variant_id="custom_software", by="usr_1", now=AUSTIN_EVENING)
    rv = await single_call.review(db, lead, **args)
    assert not rv["can_call"] and rv["overridable"] == ["simulation", "business_hours"] and not rv["may_override"]
    hours = next(c for c in rv["checks"] if c["id"] == "calling_window")
    assert hours["override"] == "business_hours" and hours["detail"].startswith("Outside business hours where they are.")
    # someone who may not override asks for nothing
    assert (await single_call.review(db, lead, **args, overrides=["simulation", "business_hours"]))["overrides"] == []
    over = await single_call.review(db, lead, **args, overrides=["business_hours", "simulation", "single_gap"], may_override=True)
    assert over["can_call"] and over["overrides"] == ["business_hours", "simulation"] and over["overridable"] == []
    assert _status(over, "simulation") == _status(over, "calling_window") == "overridden" and over["hash"] != rv["hash"]

    place = dict(lead_id="lead_0199", offer_id=WEBLAUD_ID, offer_version=1, variant_id="custom_software",
                 from_number=over["from_number"], by="usr_1", now=AUSTIN_EVENING, overrides=over["overrides"])
    with pytest.raises(single_call.SingleCallError) as e:                         # a sales manager only
        await single_call.place(db, **place, review_hash_seen=over["hash"], override_reason="They asked for 7 pm",
                                dispatch=Dispatched())
    assert e.value.status == 403
    with pytest.raises(single_call.SingleCallError) as e:                         # with a reason
        await single_call.place(db, **place, review_hash_seen=over["hash"], override_reason=" ", may_override=True,
                                dispatch=Dispatched())
    assert e.value.status == 422
    with pytest.raises(single_call.SingleCallError) as e:                         # the overrides are part of what was approved
        await single_call.place(db, **place, review_hash_seen=rv["hash"], override_reason="They asked for 7 pm",
                                may_override=True, dispatch=Dispatched())
    assert e.value.detail["code"] == "review_changed"
    assert not await db[m.ATTEMPTS].count_documents({})

    sent = Dispatched()
    placed = await single_call.place(db, **place, review_hash_seen=over["hash"], override_reason="They asked for 7 pm",
                                     may_override=True, dispatch=sent)
    attempt = await db[m.ATTEMPTS].find_one({"_id": placed["attempt_id"]})
    assert len(sent) == 1 and attempt["overrides"] == ["business_hours", "simulation"]
    assert attempt["approval"]["overrides"] == attempt["overrides"] == attempt["approval"]["material"]["overrides"]
    assert attempt["approval"]["override_reason"] == "They asked for 7 pm"
    claimed = await db[m.LEADS].find_one({"_id": "lead_0199"})
    assert claimed["gate"]["allowed"]
    # the agent's own check, seconds before dialling, builds the same campaign from the attempt
    decision = await gate.run_gate(db, single_call.gate_view(claimed), single_call.campaign_for(attempt), AUSTIN_EVENING,
                                   record=False)
    assert decision.allowed, decision.failures


async def test_the_laws_hours_and_a_no_have_no_override(offers_ready):
    db = offers_ready
    await db[m.LEADS].update_one({"_id": "lead_0199"}, {"$set": {"declined_offers": [WEBLAUD_ID]}})
    lead = await single_call.resolve(db, "lead_0199")
    rv = await single_call.review(db, lead, offer_id=WEBLAUD_ID, variant_id="custom_software", now=AUSTIN_LATE,
                                  overrides=list(single_call.OVERRIDES), may_override=True)
    assert not rv["can_call"] and rv["overrides"] == [] and rv["overridable"] == []
    hours = next(c for c in rv["checks"] if c["id"] == "calling_window")
    assert hours["status"] == "fail" and "override" not in hours
    assert hours["detail"].startswith("Outside the legal calling hours where they are.")
    assert _status(rv, "offer") == "fail" and _status(rv, "simulation") == "warn"


async def test_a_caller_sees_what_a_manager_could_override(client, offers_ready, frozen_api_clock, monkeypatch):
    monkeypatch.setattr(settings, "SALES_REQUIRE_SIMULATION", True)
    h = await _sales_user(offers_ready, "caller")
    first = client.post("/platform/sales/calls/prepare", headers=h, json={"lead_id": "lead_0199"}).json()["review"]
    assert not first["may_override"] and first["overridable"] == ["simulation"]
    body = {"lead_id": "lead_0199", "offer_id": WEBLAUD_ID, "variant_id": "custom_software"}
    r = client.post("/platform/sales/calls/review", headers=h, json={**body, "overrides": ["simulation"]})
    assert r.status_code == 200, r.text
    rv = r.json()
    assert not rv["may_override"] and rv["overrides"] == [] and rv["overridable"] == ["simulation"] and not rv["can_call"]
    assert client.post("/platform/sales/calls/review", headers=h, json={**body, "overrides": ["internal_dnc"]}).status_code == 422
    r = client.post("/platform/sales/calls", headers=h, json={
        **body, "offer_version": 1, "from_number": rv["from_number"], "review_hash": rv["hash"], "approved": True,
        "overrides": ["simulation"], "override_reason": "Tested it by hand"})
    assert r.status_code == 403 and "sales manager" in r.text


async def test_a_manager_overrides_over_the_api_and_it_is_audited(admin, offers_ready, frozen_api_clock, monkeypatch):
    db = offers_ready
    monkeypatch.setattr(settings, "SALES_REQUIRE_SIMULATION", True)
    sent = Dispatched()
    monkeypatch.setattr(dialer, "livekit_dispatch", sent)
    first = admin.post("/platform/sales/calls/prepare", json={"lead_id": "lead_0199"}).json()["review"]
    assert first["may_override"] and first["overridable"] == ["simulation"]       # the first review offers it too
    body = {"lead_id": "lead_0199", "offer_id": WEBLAUD_ID, "variant_id": "custom_software"}
    rv = admin.post("/platform/sales/calls/review", json={**body, "overrides": ["simulation"]}).json()
    assert rv["may_override"] and rv["can_call"] and rv["overrides"] == ["simulation"]
    place = {**body, "offer_version": rv["offer"]["version"], "from_number": rv["from_number"], "review_hash": rv["hash"],
             "approved": True, "overrides": ["simulation"]}
    assert admin.post("/platform/sales/calls", json=place).status_code == 422                     # no reason
    r = admin.post("/platform/sales/calls", json={**place, "override_reason": "Tested it by hand this morning"})
    assert r.status_code == 200, r.text
    assert r.json()["attempt"]["overrides"] == ["simulation"] and len(sent) == 1
    audit = await db.audit_log.find_one({"action": "sales.call.single"})
    assert audit["diff"]["overrides"] == ["simulation"]
    assert audit["diff"]["override_reason"] == "Tested it by hand this morning"
