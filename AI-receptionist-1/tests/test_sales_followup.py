# ruff: noqa: F811  (pytest fixtures shared with test_phase4_outbound are imported, then requested by name)
"""SALES_DIALER_PLAN.md Phase 3: lead reuse, the pipeline, tasks, the timeline, meeting invites,
sales email (outbox, unsubscribe) and CSV export."""

import base64
from datetime import timedelta

from config import settings
from core.outbound import dialer, followup, outcomes, sales_tools
from core.outbound import models as m
from core.outbound import offers as of
from core.outbound.offer_seeds import WEBLAUD_ID, seeds
from migrations import m0006_sales_offers
from tests.test_phase4_outbound import (  # noqa: F401  (fixtures)
    NOW, Dispatched, admin, campaign_doc, frozen_api_clock, lead_doc, ready, sales, seeded,
)


def _weblaud(**kw):
    return campaign_doc(_id="cmp_web", name="WebLaud studio", offer_id=WEBLAUD_ID, offer_version=1,
                        variant_ids=["ai_automation"], **kw)


class Mailbox(list):
    ok = True

    async def __call__(self, to, subject, text, html=None, **kw):
        self.append({"to": to, "subject": subject, "text": text, **kw})
        return self.ok


# ── Re-using a lead for another offer ────────────────────────────────────────


async def test_an_exhausted_lead_can_join_another_offers_campaign_with_fresh_attempts(seeded):
    await m0006_sales_offers.run(seeded, apply=True, now=NOW)
    await seeded[m.LEADS].update_one({}, {"$set": {"status": "exhausted", "attempts": 3, "last_outcome": "no_answer"}})
    await seeded[m.CAMPAIGNS].insert_one(_weblaud())
    result = await dialer.queue_leads(seeded, await seeded[m.CAMPAIGNS].find_one({"_id": "cmp_web"}), {}, now=NOW)
    assert result["queued"] == 1 and result["reused"] == 1
    lead = await seeded[m.LEADS].find_one({})
    assert (lead["status"], lead["attempts"], lead["campaign_id"]) == ("queued", 0, "cmp_web")
    assert lead["history"][0] == {"campaign_id": "cmp_1", "offer_id": "off_laudline", "offer_version": 0,
                                  "status": "exhausted", "attempts": 3, "last_outcome": "no_answer",
                                  "until": lead["history"][0]["until"]}
    assert (await dialer.tick(seeded, now=NOW, dispatch=Dispatched()))["dialed"] == 1     # and it is called


async def test_reuse_never_brings_back_dnc_wrong_numbers_declined_offers_or_cooling_leads(seeded):
    await m0006_sales_offers.run(seeded, apply=True, now=NOW)
    await seeded[m.LEADS].delete_many({})
    rows = [lead_doc(_id="dnc", phone="+15125550101", status="dnc"),
            lead_doc(_id="bad", phone="+15125550102", status="bad_number"),
            lead_doc(_id="declined", phone="+15125550103", status="not_interested", declined_offers=[WEBLAUD_ID]),
            lead_doc(_id="cooling", phone="+15125550104", status="not_interested", declined_offers=["off_laudline"],
                     cooldown_until=NOW + timedelta(days=60)),
            lead_doc(_id="rested", phone="+15125550105", status="not_interested", declined_offers=["off_laudline"],
                     cooldown_until=NOW - timedelta(days=1)),
            lead_doc(_id="hot", phone="+15125550106", status="interested")]
    await seeded[m.LEADS].insert_many(rows)
    await seeded[m.CAMPAIGNS].insert_one(_weblaud())
    campaign = await seeded[m.CAMPAIGNS].find_one({"_id": "cmp_web"})
    result = await dialer.queue_leads(seeded, campaign, {}, now=NOW)
    statuses = {d["_id"]: d["status"] async for d in seeded[m.LEADS].find({})}
    assert statuses == {"dnc": "dnc", "bad": "bad_number", "declined": "not_interested", "cooling": "not_interested",
                        "rested": "queued", "hot": "interested"}
    assert (result["queued"], result["resting"]) == (1, 2)
    # a lead in the pipeline only comes back when someone picks it
    picked = await dialer.queue_leads(seeded, campaign, {"_id": {"$in": ["hot"]}}, now=NOW, picked=True)
    assert picked["queued"] == 1


# ── Pipeline and tasks ───────────────────────────────────────────────────────


def test_pipeline_stage_comes_from_the_calls_unless_a_person_moved_it_on():
    assert followup.stage_of({"status": "new"}) == "new"
    assert followup.stage_of({"status": "queued", "attempts": 2}) == "contacted"
    assert followup.stage_of({"status": "interested"}) == "interested"
    assert followup.stage_of({"status": "demo_booked"}) == "meeting_booked"
    assert followup.stage_of({"status": "not_interested"}) == "lost"
    assert followup.stage_of({"status": "demo_booked", "stage": "proposal"}) == "proposal"
    assert followup.stage_of({"status": "demo_booked", "stage": "interested"}) == "meeting_booked"   # behind: ignored
    assert followup.stage_of({"status": "interested", "stage": "won"}) == "won"


async def test_an_interested_call_lands_on_the_timeline_with_a_follow_up_task(seeded):
    await dialer.tick(seeded, now=NOW, dispatch=Dispatched())
    attempt = await seeded[m.ATTEMPTS].find_one({})
    await outcomes.finish_attempt(seeded, attempt["_id"], "interested", now=NOW, summary="Owner keen; send pricing.")
    tasks = await seeded[followup.TASKS].find({}).to_list(10)
    assert len(tasks) == 1 and tasks[0]["source"] == "agent" and tasks[0]["title"].startswith("Follow up with")
    assert outcomes._aware(tasks[0]["due_at"]) == NOW + timedelta(days=followup.FOLLOW_UP_DAYS)
    kinds = [a["type"] for a in await followup.activity(seeded, attempt["lead_id"])]
    assert "call" in kinds and "task" in kinds
    # relabelling doesn't create a second task
    await outcomes.relabel_attempt(seeded, attempt["_id"], "interested", now=NOW)
    assert await seeded[followup.TASKS].count_documents({}) == 1


async def test_pipeline_tasks_notes_and_stage_over_the_api(admin, seeded, frozen_api_clock):
    await seeded[m.LEADS].update_one({}, {"$set": {"status": "interested"}})
    lead_id = (await seeded[m.LEADS].find_one({}))["_id"]
    board = admin.get("/platform/sales/pipeline").json()
    assert board["counts"]["interested"] == 1
    assert admin.patch(f"/platform/sales/leads/{lead_id}", json={"stage": "proposal"}).status_code == 200
    assert admin.patch(f"/platform/sales/leads/{lead_id}", json={"stage": "nonsense"}).status_code == 422
    assert admin.get("/platform/sales/pipeline").json()["counts"]["proposal"] == 1
    assert admin.post(f"/platform/sales/leads/{lead_id}/notes", json={"text": "Wants a quote by Friday"}).status_code == 200
    task = admin.post("/platform/sales/tasks", json={"lead_id": lead_id, "title": "Send the proposal"}).json()["task"]
    assert [t["id"] for t in admin.get("/platform/sales/tasks").json()["tasks"]] == [task["id"]]
    admin.patch(f"/platform/sales/tasks/{task['id']}", json={"done": True})
    assert admin.get("/platform/sales/tasks").json()["tasks"] == []
    detail = admin.get(f"/platform/sales/leads/{lead_id}").json()
    assert detail["lead"]["stage"] == "proposal"
    assert {a["type"] for a in detail["activity"]} >= {"note", "stage", "task"}


# ── Sales email: invites, the outbox, unsubscribe ────────────────────────────


async def test_a_booked_meeting_sends_a_calendar_invite_through_the_outbox(sales, db, monkeypatch):
    box = Mailbox()
    monkeypatch.setattr(followup, "send_email", box)
    monkeypatch.setattr(settings, "EMAIL_FROM", "WebLaud <hello@weblaud.com>")
    await db[m.LEADS].insert_one(dict(sales.lead))
    sales.offer = of.runtime(seeds()[WEBLAUD_ID], WEBLAUD_ID, 1)
    slots = await sales_tools.check_meeting_slots(sales, {})
    booked = await sales_tools.book_meeting(sales, {"slot_id": slots["slots"][0]["slot_id"], "name": "Joe Smith",
                                                    "email": "joe@example.com"})
    assert booked["success"]
    for _ in range(20):                                  # the email is sent in the background
        if box:
            break
        import asyncio
        await asyncio.sleep(0.01)
    mail = box[0]
    assert mail["subject"].startswith("Your WebLaud discovery call") and "unsubscribe" in mail["text"].lower()
    ics = base64.b64decode(mail["attachments"][0]["content"]).decode()
    assert "METHOD:REQUEST" in ics and "ATTENDEE" in ics and "joe@example.com" in ics and "hello@weblaud.com" in ics
    assert (await db[followup.OUTBOX].find_one({}))["status"] == "sent"
    lead = await db[m.LEADS].find_one({})
    assert lead["meetings"][0]["appointment_id"] == booked["appointment_id"] and sales.email_consent_at == sales.now


async def test_a_failed_email_is_retried_then_left_for_a_person(db, monkeypatch):
    box = Mailbox()
    box.ok = False
    monkeypatch.setattr(followup, "send_email", box)
    doc = await followup.send_sales_email(db, to="joe@example.com", subject="Hi", text="Hello", kind="info", now=NOW)
    assert doc["status"] == "pending" and doc["tries"] == 1
    await followup.retry_outbox(db, now=NOW + timedelta(minutes=6))
    await followup.retry_outbox(db, now=NOW + timedelta(minutes=12))
    row = await db[followup.OUTBOX].find_one({})
    assert row["status"] == "failed" and row["tries"] == followup.OUTBOX_MAX_TRIES
    box.ok = True
    await followup.retry_outbox(db, now=NOW + timedelta(minutes=30))
    assert (await db[followup.OUTBOX].find_one({}))["status"] == "failed"      # only a person retries it now


async def test_unsubscribed_addresses_never_get_sales_email(client, db, monkeypatch):
    box = Mailbox()
    monkeypatch.setattr(followup, "send_email", box)
    monkeypatch.setattr(settings, "WEBHOOK_BASE_URL", "https://api.example.com")
    link = followup.unsubscribe_link("Joe@Example.com")
    assert link.startswith("https://api.example.com/public/sales/unsubscribe?e=joe%40example.com&sig=")
    path = link.removeprefix("https://api.example.com")
    assert client.get(path.replace("sig=", "sig=0")).status_code == 400          # forged
    assert client.get(path).status_code == 200
    doc = await followup.send_sales_email(db, to="joe@example.com", subject="Hi", text="Hello", kind="info", now=NOW)
    assert doc["status"] == "suppressed" and box == []


async def test_csv_exports_are_spreadsheet_safe(admin, seeded):
    await seeded[m.LEADS].update_one({}, {"$set": {"business_name": "=HYPERLINK(\"x\")", "status": "interested"}})
    r = admin.get("/platform/sales/leads.csv", params={"stage": "interested"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert len(lines) == 2 and lines[1].startswith("\"'=HYPERLINK")                   # the formula is neutralised
    assert admin.get("/platform/sales/leads.csv", params={"stage": "won"}).text.strip().count("\n") == 0
    assert admin.get("/platform/sales/attempts.csv").status_code == 200
