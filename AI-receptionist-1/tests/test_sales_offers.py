# ruff: noqa: F811  (pytest fixtures shared with test_phase4_outbound are imported, then requested by name)
"""
SALES_DIALER_PLAN.md Phase 1A: offers (sell anything, edited in the dashboard), "not interested" is
not an opt-out, consent covers only the company it named, and the date checks on the agent's tools.
"""

from datetime import timedelta

import pytest

from config import settings
from core.outbound import dialer, gate, outcomes, registry, sales_tools
from core.outbound import models as m
from core.outbound import offers as of
from core.outbound.gate import GateFacts
from core.outbound.offer_seeds import LAUDLINE_ID, WEBLAUD_ID, seeds
from migrations import m0006_sales_offers
from tests.test_phase4_outbound import (  # noqa: F401  (fixtures)
    AUSTIN, FRESH, NOW, Dispatched, admin, campaign_doc, decide, frozen_api_clock, lead_doc, ready, sales, seeded,
)


async def _seed_offers(db):
    await m0006_sales_offers.run(db, apply=True, now=NOW)


def _weblaud_campaign(**kw):
    return campaign_doc(offer_id=WEBLAUD_ID, offer_version=1, variant_ids=["ai_automation", "custom_software"], **kw)


# ── Offers: storage, versions, the API ───────────────────────────────────────


async def test_migration_seeds_published_offers_once_and_backfills_reviews(db):
    await db[m.CAMPAIGNS].insert_one(campaign_doc(legal_review={"confirmed_by": "usr_1", "at": NOW, "note": ""}))
    first = await m0006_sales_offers.run(db, apply=True, now=NOW)
    assert first == {"offers_seeded": 2, "offers_existing": 0, "reviews_backfilled": 1}
    assert (await db[of.OFFERS].find_one({"_id": WEBLAUD_ID}))["published_version"] == 1
    assert await db[of.OFFER_VERSIONS].find_one({"_id": f"{LAUDLINE_ID}@1"})
    again = await m0006_sales_offers.run(db, apply=True, now=NOW)
    assert again == {"offers_seeded": 0, "offers_existing": 2, "reviews_backfilled": 0}
    campaign = await db[m.CAMPAIGNS].find_one({})
    assert m.legal_review_valid(campaign) and campaign["legal_review"]["material"]["persona_ids"] == ["sales_v1"]


async def test_publish_creates_an_immutable_version_and_campaigns_pin_it(admin, db, ready):
    body = {**seeds()[WEBLAUD_ID], "name": "WebLaud: mobile apps"}
    created = admin.post("/platform/sales/offers", json=body)
    assert created.status_code == 200, created.text
    oid = created.json()["offer"]["id"]
    # a campaign can't use an offer until a version is published
    assert admin.post("/platform/sales/campaigns", json={"name": "Apps", "from_numbers": ["+15125550100"],
                                                         "offer_id": oid, "offer_version": 1}).status_code == 422
    assert admin.post(f"/platform/sales/offers/{oid}/publish").json()["version"] == 1
    camp = admin.post("/platform/sales/campaigns", json={"name": "Apps", "from_numbers": ["+15125550100"],
                                                         "offer_id": oid, "offer_version": 1})
    assert camp.status_code == 200, camp.text
    assert camp.json()["campaign"]["variant_ids"] == ["ai_automation"]          # defaults to the first script
    cid = camp.json()["campaign"]["id"]
    assert admin.patch(f"/platform/sales/campaigns/{cid}", json={"variant_ids": ["nope"]}).status_code == 422

    # editing the draft and publishing v2 doesn't touch v1, or the campaign pinned to it
    changed = {**body, "what_we_sell": "We build cross-platform mobile apps with Flutter, in fixed-price sprints."}
    assert admin.patch(f"/platform/sales/offers/{oid}", json=changed).status_code == 200
    assert admin.post(f"/platform/sales/offers/{oid}/publish").json()["version"] == 2
    assert admin.post(f"/platform/sales/offers/{oid}/publish").json()["version"] == 2   # nothing new: no v3
    v1 = await db[of.OFFER_VERSIONS].find_one({"_id": f"{oid}@1"})
    assert "Flutter" not in v1["settings"]["what_we_sell"]
    assert (await db[m.CAMPAIGNS].find_one({"_id": cid}))["offer_version"] == 1
    detail = admin.get(f"/platform/sales/offers/{oid}").json()
    assert [v["version"] for v in detail["versions"]] == [2, 1] and detail["campaigns"][0]["id"] == cid

    # publishing refuses an offer whose opener doesn't disclose the AI
    bad = {**changed, "variants": [{**body["variants"][0], "cold_opener": "Hi, it's Sam from WebLaud. Got a sec? "
                                    "If you'd rather not get calls like this, just say so."}]}
    admin.patch(f"/platform/sales/offers/{oid}", json=bad)
    refused = admin.post(f"/platform/sales/offers/{oid}/publish")
    assert refused.status_code == 422 and "does not say it is an AI" in refused.json()["detail"]

    preview = admin.post(f"/platform/sales/offers/{oid}/preview", json={"offer": body}).json()
    assert preview["opener"].startswith("Hi, this is") and "Harbour Dental Studio" in preview["opener"]
    assert "<offer>" in preview["prompt"] and preview["problems"] == [] and preview["approx_tokens"] > 100

    # a half-filled draft previews as a checklist in the editor's words, not a 422
    unfinished = admin.post(f"/platform/sales/offers/{oid}/preview",
                            json={"offer": {**body, "name": "", "what_we_sell": "Apps"}})
    assert unfinished.status_code == 200 and unfinished.json()["prompt"] == ""
    assert unfinished.json()["problems"] == ["Offer name is required", "What you sell needs at least 10 characters"]

    # archive, never delete; an archived offer can't be newly attached
    assert admin.post(f"/platform/sales/offers/{oid}/archive").status_code == 200
    assert admin.post("/platform/sales/campaigns", json={"name": "Apps 2", "from_numbers": ["+15125550100"],
                                                         "offer_id": oid, "offer_version": 2}).status_code == 422
    assert all(o["id"] != oid for o in admin.get("/platform/sales/offers").json()["offers"])
    assert admin.post(f"/platform/sales/offers/{oid}/duplicate").json()["offer"]["status"] == "draft"


async def test_moving_a_campaign_to_a_new_offer_version_needs_a_fresh_review(admin, db, ready):
    await _seed_offers(db)
    cid = admin.post("/platform/sales/campaigns", json={"name": "Studio", "from_numbers": ["+15125550100"],
                                                        "offer_id": WEBLAUD_ID, "offer_version": 1}).json()["campaign"]["id"]
    admin.post(f"/platform/sales/campaigns/{cid}/start", json={"legal_review_confirmed": True})
    admin.patch(f"/platform/sales/offers/{WEBLAUD_ID}", json={**seeds()[WEBLAUD_ID], "ideal_customer": "Agencies"})
    admin.post(f"/platform/sales/offers/{WEBLAUD_ID}/publish")
    assert admin.patch(f"/platform/sales/campaigns/{cid}", json={"offer_version": 2}).status_code == 409   # running
    admin.post(f"/platform/sales/campaigns/{cid}/pause")
    moved = admin.patch(f"/platform/sales/campaigns/{cid}", json={"offer_version": 2}).json()["campaign"]
    assert moved["offer_version"] == 2 and moved["legal_review"] is None


async def test_dial_records_the_offer_version_and_variant(seeded):
    await _seed_offers(seeded)
    await seeded[m.CAMPAIGNS].replace_one({}, _weblaud_campaign())
    sent = Dispatched()
    assert (await dialer.tick(seeded, now=NOW, dispatch=sent))["dialed"] == 1
    attempt = await seeded[m.ATTEMPTS].find_one({})
    assert (attempt["offer_id"], attempt["offer_version"]) == (WEBLAUD_ID, 1)
    assert attempt["variant_id"] in ("ai_automation", "custom_software") and attempt["persona_id"] == attempt["variant_id"]
    offer = await of.offer_for(seeded, attempt["offer_id"], attempt["offer_version"])
    assert offer["company_name"] == "WebLaud" and of.variant(offer, attempt["variant_id"])["id"] == attempt["variant_id"]
    # old campaigns keep selling the built-in LaudLine offer
    legacy = await of.offer_for(seeded, None, None)
    assert legacy["id"] == LAUDLINE_ID and legacy["version"] == 0


# ── "Not interested" is not an opt-out ───────────────────────────────────────


async def test_not_interested_suppresses_the_offer_and_rests_the_lead_but_is_not_dnc(seeded):
    await seeded[m.CAMPAIGNS].replace_one({}, _weblaud_campaign())
    await dialer.tick(seeded, now=NOW, dispatch=Dispatched())
    attempt = await seeded[m.ATTEMPTS].find_one({})
    await outcomes.finish_attempt(seeded, attempt["_id"], "not_interested", now=NOW, reason="happy with their agency")
    lead = await seeded[m.LEADS].find_one({})
    assert lead["status"] == "not_interested" and lead["declined_offers"] == [WEBLAUD_ID]
    assert outcomes._aware(lead["cooldown_until"]) == NOW + timedelta(days=settings.SALES_NOT_INTERESTED_COOLDOWN_DAYS)
    assert lead["offer_history"][0]["reason"] == "happy with their agency"
    assert not await registry.on_internal_dnc(seeded, AUSTIN)                         # never the global list
    # re-applying the same attempt's outcome doesn't add a second history entry
    await outcomes.relabel_attempt(seeded, attempt["_id"], "not_interested", now=NOW)
    assert len((await seeded[m.LEADS].find_one({}))["offer_history"]) == 1


def test_gate_blocks_a_declined_offer_for_good_and_other_offers_during_the_cooldown():
    resting = lead_doc(declined_offers=[WEBLAUD_ID], cooldown_until=NOW + timedelta(days=30))
    same = decide(resting, _weblaud_campaign())
    assert same.failures == ["offer_declined"] and same.permanent and same.lead_status() == "not_interested"
    other = decide(resting, campaign_doc())                                      # the LaudLine offer
    assert other.failures == ["cooldown"] and other.lead_status() == "not_interested"
    later = decide(resting, campaign_doc(), now=NOW + timedelta(days=31, hours=0))
    assert "cooldown" not in later.failures
    assert "offer_declined" in decide(resting, _weblaud_campaign(), now=NOW + timedelta(days=400)).failures
    # a lead that is also on a do-not-call list is blocked, not merely resting
    both = decide(resting, campaign_doc(), facts=GateFacts(registry_status=FRESH, registry_hits=("us_national",)))
    assert both.lead_status() == "blocked"


async def test_relabelling_away_from_not_interested_lifts_the_suppression(seeded):
    await seeded[m.CAMPAIGNS].replace_one({}, _weblaud_campaign())
    await dialer.tick(seeded, now=NOW, dispatch=Dispatched())
    attempt = await seeded[m.ATTEMPTS].find_one({})
    await outcomes.finish_attempt(seeded, attempt["_id"], "not_interested", now=NOW)
    await outcomes.relabel_attempt(seeded, attempt["_id"], "interested", now=NOW)
    lead = await seeded[m.LEADS].find_one({})
    assert lead["status"] == "interested" and lead["declined_offers"] == [] and lead["cooldown_until"] is None
    assert lead["offer_history"] == []


# ── Consent covers the company it named ──────────────────────────────────────


async def test_consent_only_covers_the_company_it_named(db, monkeypatch):
    monkeypatch.setattr(settings, "SALES_COMPANY_NAME", "LaudLine")
    consent = await registry.record_consent(db, phone=AUSTIN, type="written", language_shown="…", source="form:x",
                                            scope={"company": "LaudLine", "offer_id": LAUDLINE_ID})
    lead = lead_doc()
    laudline = decide(lead, campaign_doc(mode="ai_consented"),
                      GateFacts(consent=consent, offer_company="LaudLine"))
    assert laudline.allowed, laudline.checks
    weblaud = decide(lead, campaign_doc(mode="ai_consented", offer_id=WEBLAUD_ID, offer_version=1),
                     GateFacts(consent=consent, offer_company="WebLaud"))
    assert weblaud.failures == ["consent_scope"] and weblaud.permanent
    # consent recorded before offers existed was for the built-in (LaudLine) offer
    old = {k: v for k, v in consent.items() if k != "scope"}
    assert gate.consent_covers(old, "") and not gate.consent_covers(old, "WebLaud")


async def test_the_consent_form_shows_and_records_the_consent_campaigns_offer(client, db, ready, monkeypatch, frozen_api_clock):
    await _seed_offers(db)
    await db[m.CAMPAIGNS].insert_one(campaign_doc(_id="cmp_form", mode="ai_consented", offer_id=WEBLAUD_ID,
                                                  offer_version=1, variant_ids=["ai_automation"]))
    monkeypatch.setattr(settings, "SALES_CONSENT_CAMPAIGN_ID", "cmp_form")
    info = client.get("/public/demo-call/consent").json()
    assert info["company"] == "WebLaud" and "custom AI and software" in info["text"]
    assert info["version"].endswith(f"{WEBLAUD_ID}@1")
    body = {"phone": "512-555-0177", "consent": True, "consent_version": info["version"], "page": "/studio"}
    assert client.post("/public/demo-call", json={**body, "consent_version": "2026-11-v1"}).status_code == 422
    assert client.post("/public/demo-call", json=body).status_code == 200
    record = await db[m.CONSENT].find_one({})
    assert record["scope"] == {"company": "WebLaud", "offer_id": WEBLAUD_ID} and record["language_shown"] == info["text"]


# ── The agent's tools: dates it can't get wrong silently ─────────────────────


async def test_a_wrong_weekday_is_caught_before_anything_is_booked(sales, db):
    await db[m.LEADS].insert_one(dict(sales.lead))
    wrong = await sales_tools.schedule_callback(sales, {"when": "2026-11-19T14:30", "weekday": "Friday"})
    assert wrong["error"] == "weekday_mismatch" and "Thursday" in wrong["message"] and sales.outcome is None
    right = await sales_tools.schedule_callback(sales, {"when": "2026-11-19T14:30", "weekday": "thursday"})
    assert right["success"]
    slots = await sales_tools.check_meeting_slots(sales, {"date": "2026-11-19", "weekday": "Wednesday"})
    assert slots["error"] == "weekday_mismatch"


async def test_a_callback_outside_calling_hours_is_refused_with_the_next_open_time(sales, db):
    await db[m.LEADS].insert_one(dict(sales.lead))
    night = await sales_tools.schedule_callback(sales, {"when": "2026-11-19T22:00", "weekday": "Thursday"})
    assert night["error"] == "outside_hours" and "nearest time" in night["message"] and sales.callback_at is None
    sunday = await sales_tools.schedule_callback(sales, {"when": "2026-11-22T11:00"})
    assert sunday["error"] == "outside_hours"


async def test_not_interested_tool_never_touches_the_do_not_call_list(sales, db):
    await db[m.LEADS].insert_one(dict(sales.lead))
    done = await sales_tools.mark_not_interested(sales, {"reason": "no budget this year"})
    assert done["success"] and sales.outcome == "not_interested" and sales.not_interested_reason == "no budget this year"
    assert not sales.opted_out and not await db[m.DNC].count_documents({})


async def test_offer_emails_are_rendered_from_the_offer(sales, db, monkeypatch):
    sent = []

    async def fake_send(to, subject, text, html=None, **kw):
        sent.append((to, subject, text))
        return True
    from core.outbound import followup
    monkeypatch.setattr(followup, "send_email", fake_send)
    monkeypatch.setattr(settings, "SALES_EMAIL_FOOTER", "WebLaud LLC, Cheyenne WY. Reply STOP to opt out.")
    await db[m.LEADS].insert_one(dict(sales.lead))
    sales.offer = of.runtime(seeds()[WEBLAUD_ID], WEBLAUD_ID, 1)
    await sales_tools.send_info_email(sales, {"email": "joe@example.com"})
    to, subject, text = sent[0]
    assert subject == "WebLaud: custom AI and software for Joe's Plumbing"
    assert "fixed-price sprint" in text and "Reply STOP to opt out." in text and "{" not in text
    assert "Unsubscribe" in text or "unsubscribe" in text


async def test_a_dry_run_writes_nothing(sales, db):
    await db[m.LEADS].insert_one(dict(sales.lead))
    sales.dry_run = True
    await sales_tools.add_to_dnc(sales, {"reason": "test"})
    await sales_tools.send_info_email(sales, {"email": "joe@example.com"})
    slots = await sales_tools.check_meeting_slots(sales, {})
    booked = await sales_tools.book_meeting(sales, {"slot_id": slots["slots"][0]["slot_id"], "name": "Joe",
                                                    "email": "joe@example.com"})
    assert booked["success"] and booked["appointment_id"] == "test"
    assert not await db[m.DNC].count_documents({}) and not await db.appointments.count_documents({})
    assert (await db[m.LEADS].find_one({}))["status"] == "queued"


@pytest.mark.parametrize("offer_id", [LAUDLINE_ID, WEBLAUD_ID])
def test_seeded_offers_fit_the_prompt_budget(offer_id):
    s = of.OfferSettings.model_validate(seeds()[offer_id])
    assert of.text_size(s) < of.MAX_OFFER_CHARS


async def test_a_test_call_summons_the_sales_agent_in_a_dry_run(admin, db, monkeypatch):
    import json

    import jwt

    monkeypatch.setattr(settings, "LIVEKIT_URL", "wss://lk.example")
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", "key")
    monkeypatch.setattr(settings, "LIVEKIT_API_SECRET", "secret-0123456789abcdef0123456789abcdef")
    await _seed_offers(db)
    r = admin.post(f"/platform/sales/offers/{WEBLAUD_ID}/test-call", json={"variant_id": "custom_software"})
    assert r.status_code == 200, r.text
    claims = jwt.decode(r.json()["token"], options={"verify_signature": False})
    dispatch = claims["roomConfig"]["agents"][0]
    assert dispatch["agentName"] == settings.SALES_AGENT_NAME and claims["sub"] == "lead"
    test_id = json.loads(dispatch["metadata"])["test_call_id"]
    doc = await db["sales_test_calls"].find_one({"_id": test_id})
    assert doc["variant_id"] == "custom_software" and doc["settings"]["company_name"] == "WebLaud"
    assert r.json()["room"] == f"salestest-{test_id}"
    # nothing about a real lead or call is written
    assert not await db[m.ATTEMPTS].count_documents({}) and not await db[m.LEADS].count_documents({})
    # an offer that would fail publishing can't be test-called either
    bad = {**seeds()[WEBLAUD_ID], "variants": [{**seeds()[WEBLAUD_ID]["variants"][0], "cold_opener": "Hello there, got a minute for us?"}]}
    assert admin.post(f"/platform/sales/offers/{WEBLAUD_ID}/test-call", json={"offer": bad}).status_code == 422
