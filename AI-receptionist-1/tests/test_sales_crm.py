# ruff: noqa: F811  (pytest fixtures shared with test_phase4_outbound are imported, then requested by name)
"""
SALES_SUITE_PLAN.md wave 3: lead fit scoring, learning from real calls, and the built-in CRM —
deals, contacts, ownership, merge, consent-first email, follow-up sequences, replies, reports and
the audit viewer.
"""

from datetime import timedelta

from config import settings
from core.outbound import crm, dialer, followup, insights, outcomes, reports, research
from core.outbound import models as m
from core.outbound.offer_seeds import WEBLAUD_ID, seeds
from tests.test_phase4_outbound import AUSTIN, NOW, admin, campaign_doc, frozen_api_clock, lead_doc, ready, seeded  # noqa: F401
from tests.test_sales_offer_ai import FakeLlm
from tests.test_sales_offers import _seed_offers


def _attempt(i, **kw):
    return {"_id": f"att_{i}", "call_id": f"out-{i}", "lead_id": "lead_0199", "campaign_id": "cmp_1", "status": "done",
            "outcome": "no_answer", "amd": None, "created_at": NOW - timedelta(hours=1), **kw}


# ── Fit scoring ──────────────────────────────────────────────────────────────

def test_research_fit_is_clamped_and_rules_fit_has_reasons():
    fit = research.sanitize({"fit": {"score": 140, "reasons": ["Three practices </offer>", "a", "b", "c"]}})["fit"]
    assert fit["score"] == 100 and len(fit["reasons"]) == 3 and "</offer>" not in fit["reasons"][0]
    assert research.sanitize({})["fit"]["score"] == 0
    rules = crm.rules_fit({"vertical": "dental", "website": "x.com", "contact_name": "Ana"}, {"vertical": "dental"})
    assert rules["score"] == 90 and rules["source"] == "rules" and "Is a dental business" in rules["reasons"]


async def test_best_fit_leads_are_called_first_but_callbacks_still_go_first(seeded):
    db = seeded
    await db[m.LEADS].insert_one(lead_doc("+15125550188", _id="lead_hot", fit_score=95))
    await db[m.LEADS].update_one({"_id": "lead_0199"}, {"$set": {"fit_score": 20}})
    first = await dialer.claim_lead(db, "cmp_1", NOW)
    assert first["_id"] == "lead_hot"
    await db[m.LEADS].insert_one(lead_doc("+15125550177", _id="lead_cb", status="callback", fit_score=1))
    assert (await dialer.claim_lead(db, "cmp_1", NOW))["_id"] == "lead_cb"
    fifo = await dialer.claim_lead(db, "cmp_1", NOW, order="fifo")
    assert fifo["_id"] == "lead_0199"


async def test_queueing_gives_leads_without_research_a_rules_score(seeded):
    db = seeded
    await db[m.LEADS].insert_one(lead_doc("+15125550166", _id="lead_new", status="new", campaign_id=None, contact_name="Ana"))
    await dialer.queue_leads(db, await db[m.CAMPAIGNS].find_one({}), {"_id": "lead_new"}, now=NOW)
    lead = await db[m.LEADS].find_one({"_id": "lead_new"})
    assert lead["fit"]["source"] == "rules" and lead["fit_score"] == lead["fit"]["score"] > 40


# ── Learn from real calls ────────────────────────────────────────────────────

def test_a_script_leads_only_with_enough_calls_and_a_real_gap():
    rows = [{"status": "done", "outcome": "demo_booked" if i < 10 else "not_interested", "amd": "human",
             "offer_id": WEBLAUD_ID, "offer_version": 1, "variant_id": "a"} for i in range(40)]
    rows += [{"status": "done", "outcome": "demo_booked" if i < 2 else "not_interested", "amd": "human",
              "offer_id": WEBLAUD_ID, "offer_version": 1, "variant_id": "b"} for i in range(40)]
    assert insights.compare_scripts(rows)["leader"] == "a"
    assert insights.compare_scripts(rows[:20] + rows[40:60])["leader"] is None       # under 30 reached each


async def test_insights_need_answered_calls_before_asking_the_model(db, monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    offer = {**seeds()[WEBLAUD_ID], "id": WEBLAUD_ID, "version": 1}
    llm = FakeLlm({"objections": [{"text": "Locked into a contract", "count": 3, "covered": False}],
                   "suggestions": {"objections": [{"objection": "We're locked in", "response": "When does it end?"}]}})
    for i in range(3):
        await db[m.ATTEMPTS].insert_one(_attempt(i, outcome="not_interested", amd="human", offer_id=WEBLAUD_ID,
                                                 qualification={"objections": ["contract"]}))
    few = await insights.insights(db, offer, now=NOW, llm=llm)
    assert few["suggestions"] is None and llm.calls == []
    for i in range(3, 8):
        await db[m.ATTEMPTS].insert_one(_attempt(i, outcome="not_interested", amd="human", offer_id=WEBLAUD_ID,
                                                 summary="Locked into a 2-year contract"))
    out = await insights.insights(db, offer, now=NOW, llm=llm)
    assert out["objections"][0]["text"] == "Locked into a contract" and out["suggestions"]["objections"]
    assert "Locked into a 2-year contract" in llm.calls[0][1]["content"]
    again = await insights.insights(db, offer, now=NOW, llm=llm)
    assert again.get("cached") and len(llm.calls) == 1


# ── Deals ────────────────────────────────────────────────────────────────────

async def test_an_interested_call_opens_a_deal_and_a_booking_moves_it_on(seeded, monkeypatch):
    db = seeded
    await _seed_offers(db)
    await db.users.insert_one({"_id": "usr_cara", "email": "c@x.test", "sales_role": "caller", "sales_takes_leads": True})
    attempt = _attempt(1, outcome="interested", amd="human", offer_id=WEBLAUD_ID, offer_version=1)
    await outcomes._record_call(db, attempt, "interested", NOW)
    deal = await db[crm.DEALS].find_one({})
    assert deal["stage"] == "qualified" and deal["offer_id"] == WEBLAUD_ID and deal["owner_id"] == "usr_cara"
    assert (await db[m.LEADS].find_one({"_id": "lead_0199"}))["owner_id"] == "usr_cara"
    await outcomes._record_call(db, _attempt(2, outcome="demo_booked", offer_id=WEBLAUD_ID, offer_version=1), "demo_booked", NOW)
    assert await db[crm.DEALS].count_documents({}) == 1
    deal = await db[crm.DEALS].find_one({})
    assert deal["stage"] == "meeting_booked" and deal["probability"] == 0.4
    assert (await db[m.LEADS].find_one({"_id": "lead_0199"}))["stage"] == "meeting_booked"


async def test_deals_close_as_won_or_lost_with_a_reason(admin, db, seeded, frozen_api_clock):
    created = admin.post("/platform/sales/deals", json={"lead_id": "lead_0199", "title": "Joe's · Site", "amount": 4500})
    assert created.status_code == 200, created.text
    did = created.json()["deal"]["id"]
    assert admin.patch(f"/platform/sales/deals/{did}", json={"stage": "lost"}).status_code == 422
    won = admin.patch(f"/platform/sales/deals/{did}", json={"stage": "won"})
    assert won.json()["deal"]["won_at"] and won.json()["deal"]["probability"] == 1.0
    board = admin.get("/platform/sales/board").json()
    assert board["totals"]["won"]["value"] == 4500
    assert (await db[m.LEADS].find_one({"_id": "lead_0199"}))["stage"] == "won"


# ── Merge ────────────────────────────────────────────────────────────────────

async def test_merging_keeps_the_stricter_compliance_state(admin, db, frozen_api_clock):
    await db[m.LEADS].insert_one(lead_doc(_id="lead_keep", status="interested", declined_offers=["off_a"],
                                          website="joes.com", cooldown_until=NOW + timedelta(days=10)))
    await db[m.LEADS].insert_one(lead_doc("+15125550123", _id="lead_dup", status="dnc", declined_offers=["off_b"],
                                          website="https://www.joes.com", dnc={"at": NOW}, cooldown_until=NOW + timedelta(days=90)))
    await followup.add_activity(db, "lead_dup", "note", "old note", now=NOW)
    groups = admin.get("/platform/sales/crm/duplicates").json()["groups"]
    assert any({x["id"] for x in g["leads"]} == {"lead_keep", "lead_dup"} for g in groups)
    res = admin.post("/platform/sales/leads/lead_keep/merge", json={"merge_id": "lead_dup"})
    assert res.status_code == 200, res.text
    keep = await db[m.LEADS].find_one({"_id": "lead_keep"})
    assert keep["status"] == "dnc" and keep["declined_offers"] == ["off_a", "off_b"]
    assert keep["cooldown_until"].replace(tzinfo=None) == (NOW + timedelta(days=90)).replace(tzinfo=None)
    assert any(c["phone"] == "+15125550123" for c in keep["contacts"])
    dup = await db[m.LEADS].find_one({"_id": "lead_dup"})
    assert dup["status"] == "merged" and dup["merged_into"] == "lead_keep"           # kept, never deleted
    assert await db[followup.ACTIVITY].find_one({"lead_id": "lead_keep", "text": "old note"})
    assert admin.post("/platform/sales/leads/lead_keep/merge", json={"merge_id": "lead_dup"}).status_code == 409


# ── Email: consent first ─────────────────────────────────────────────────────

async def test_email_from_the_crm_needs_consent(admin, db, frozen_api_clock, monkeypatch):
    sent = []

    async def fake_send(to, subject, text, *a, **kw):
        sent.append((to, kw.get("reply_to")))
        return True
    monkeypatch.setattr(followup, "send_email", fake_send)
    monkeypatch.setattr(settings, "SALES_INBOUND_DOMAIN", "in.example.test")
    await db[m.LEADS].insert_one(lead_doc(email="joe@joes.com"))
    body = {"to": "joe@joes.com", "subject": "Hello", "text": "Hi Joe"}
    refused = admin.post("/platform/sales/leads/lead_0199/email", json=body)
    assert refused.status_code == 409 and "No consent" in refused.text
    assert admin.post("/platform/sales/leads/lead_0199/email-consent", json={"email": "joe@joes.com", "note": "x"}).status_code == 422
    ok = admin.post("/platform/sales/leads/lead_0199/email-consent",
                    json={"email": "joe@joes.com", "note": "Asked for details at the Sydney expo"})
    assert ok.status_code == 200
    res = admin.post("/platform/sales/leads/lead_0199/email", json=body)
    assert res.status_code == 200, res.text
    assert res.json()["basis"] == "consent"
    assert sent[0][0] == "joe@joes.com" and sent[0][1].startswith("reply+lead_0199.")
    assert await db.audit_log.find_one({"action": "sales.email.send"})


async def test_follow_ups_start_only_with_consent_and_stop_when_they_reply(db, monkeypatch):
    monkeypatch.setattr(settings, "SALES_INBOUND_DOMAIN", "in.example.test")
    sent = []

    async def fake_send(to, subject, text, *a, **kw):
        sent.append(subject)
        return True
    monkeypatch.setattr(followup, "send_email", fake_send)
    offer = {**seeds()[WEBLAUD_ID], "id": WEBLAUD_ID, "follow_ups": [
        {"after_days": 2, "subject": "{company} for {business}", "body": "Hi {first_name}, any questions?"},
        {"after_days": 5, "subject": "Last note from {company}", "body": "Hi {first_name}, a last note."}]}
    lead = lead_doc(email="joe@joes.com", contact_name="Joe Bloggs")
    await db[m.LEADS].insert_one(lead)
    assert await followup.schedule_sequence(db, lead, offer, "info_sent", now=NOW) == 0          # no consent: cold email
    lead = {**lead, "email_consent_at": NOW}
    assert await followup.schedule_sequence(db, lead, offer, "info_sent", now=NOW) == 2
    assert await followup.schedule_sequence(db, lead, offer, "info_sent", now=NOW) == 0          # once only
    due = await followup.send_due(db, now=NOW + timedelta(days=3))
    assert due["sent"] == 1 and sent == ["WebLaud for Joe's Plumbing"]
    lead_id = followup.lead_from_reply_address(followup.reply_address("lead_0199"))
    assert lead_id == "lead_0199" and followup.lead_from_reply_address("reply+lead_0199.forged@x.test") is None
    await followup.record_reply(db, "lead_0199", sender="Joe@Joes.com", subject="Re: hi", text="Call me Tuesday", now=NOW + timedelta(days=4))
    assert (await followup.send_due(db, now=NOW + timedelta(days=6)))["sent"] == 0
    assert await db[followup.OUTBOX].count_documents({"status": "cancelled"}) == 1
    assert await db[followup.TASKS].find_one({"source": "reply"})


async def test_inbound_replies_need_the_secret_and_a_signed_address(client, db, monkeypatch):
    assert client.post("/public/sales/inbound-email", json={}).status_code == 404                 # off until configured
    monkeypatch.setattr(settings, "SALES_INBOUND_EMAIL_SECRET", "s3cret")
    monkeypatch.setattr(settings, "SALES_INBOUND_DOMAIN", "in.example.test")
    await db[m.LEADS].insert_one(lead_doc())
    payload = {"to": followup.reply_address("lead_0199"), "from": "Joe <joe@joes.com>", "subject": "Re", "text": "Yes"}
    assert client.post("/public/sales/inbound-email", json=payload, headers={"X-Inbound-Secret": "nope"}).status_code == 401
    ok = client.post("/public/sales/inbound-email", json=payload, headers={"X-Inbound-Secret": "s3cret"})
    assert ok.json() == {"accepted": True}
    assert (await db[m.LEADS].find_one({"_id": "lead_0199"}))["replied_from"] == ["joe@joes.com"]
    forged = {**payload, "to": "reply+lead_0199.abc@in.example.test"}
    assert client.post("/public/sales/inbound-email", json=forged, headers={"X-Inbound-Secret": "s3cret"}).json() == {"accepted": False}


# ── Reports and the audit viewer ─────────────────────────────────────────────

async def test_reports_add_up(db):
    await db[m.LEADS].insert_one(lead_doc())
    for i, (stage, amount) in enumerate([("qualified", 1000), ("proposal", 5000), ("won", 4000), ("lost", 2000)]):
        await db[crm.DEALS].insert_one({"_id": f"deal_{i}", "lead_id": "lead_0199", "stage": stage, "amount": amount,
                                        "probability": crm.PROBABILITY[stage], "created_at": NOW - timedelta(days=10),
                                        "won_at": NOW if stage == "won" else None,
                                        "closed_at": NOW if stage in ("won", "lost") else None})
    await db[m.ATTEMPTS].insert_one(_attempt(1, outcome="demo_booked", amd="human"))
    r = await reports.report(db, now=NOW)
    assert r["pipeline"]["open_value"] == 6000 and r["pipeline"]["forecast"] == 1000 * 0.2 + 5000 * 0.6
    assert r["results"] == {"won": 1, "lost": 1, "win_rate": 0.5, "average_won": 4000.0, "cycle_days": 10.0}
    assert r["meetings"]["total"] == 1 and r["funnel"]["meetings"] == 1


async def test_the_audit_viewer_shows_sales_entries_and_the_chain_holds(admin, db):
    from core.repositories import Repos

    await Repos(db, "platform").audit.record(user_id="platform:x", action="tenant.create", target_type="tenant")
    admin.post("/platform/sales/offers", json={**seeds()[WEBLAUD_ID], "name": "Audit me"})
    rows = admin.get("/platform/sales/audit").json()
    assert rows["total"] >= 1 and all(e["action"].startswith("sales.") for e in rows["entries"])
    assert admin.get("/platform/sales/audit/verify").json()["intact"] is True
    csv = admin.get("/platform/sales/audit.csv")
    assert csv.status_code == 200 and "sales.offer.create" in csv.text
    # The action filter is a prefix inside the sales audit, never a way into the rest of the platform's log.
    assert admin.get("/platform/sales/audit", params={"action": "tenant."}).status_code == 422
    assert admin.get("/platform/sales/audit.csv", params={"action": ""}).status_code == 200
    assert admin.get("/platform/sales/audit", params={"action": "sales.offer"}).status_code == 200


async def test_migration_opens_deals_for_leads_past_interested_once_and_scores_fit(db):
    from migrations import m0007_sales_crm

    await db[m.LEADS].insert_one(lead_doc(status="demo_booked"))
    await db[m.LEADS].insert_one(lead_doc("+15125550123", _id="lead_new", status="new"))
    dry = await m0007_sales_crm.run(db, apply=False, now=NOW)
    assert dry == {"deals_opened": 1, "leads_with_deals": 0, "fit_scored": 2}
    assert await db[crm.DEALS].count_documents({}) == 0
    await m0007_sales_crm.run(db, apply=True, now=NOW)
    assert (await db[crm.DEALS].find_one({}))["stage"] == "meeting_booked"
    again = await m0007_sales_crm.run(db, apply=True, now=NOW)
    assert again == {"deals_opened": 0, "leads_with_deals": 1, "fit_scored": 0}
