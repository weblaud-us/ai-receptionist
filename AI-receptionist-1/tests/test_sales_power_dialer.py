# ruff: noqa: F811  (pytest fixtures shared with test_phase4_outbound are imported, then requested by name)
"""SALES_DIALER_PLAN.md Phase 4: the human-assisted power dialer — call context and the wrap-up form."""

from datetime import timedelta

from core.outbound import dialer, followup, outcomes, registry
from core.outbound import models as m
from core.outbound.offer_seeds import WEBLAUD_ID
from migrations import m0006_sales_offers
from tests.test_phase4_outbound import (  # noqa: F401  (fixtures)
    AUSTIN, NOW, Dispatched, admin, campaign_doc, frozen_api_clock, lead_doc, ready, seeded,
)


async def _human_call(db, **campaign):
    await m0006_sales_offers.run(db, apply=True, now=NOW)
    await db[m.CAMPAIGNS].replace_one({}, campaign_doc(mode="human_assisted", **campaign))
    result = await dialer.dial_next(db, await db[m.CAMPAIGNS].find_one({}), operator="usr_1", dispatch=Dispatched(), now=NOW)
    assert result["dialed"], result
    return result["attempt_id"]


async def test_the_context_shows_the_lead_research_and_a_cheat_sheet(admin, seeded, frozen_api_clock):
    await seeded[m.LEADS].update_one({}, {"$set": {"research": {"status": "done", "data": {"summary": "Plumbers in Austin"}}}})
    attempt_id = await _human_call(seeded, offer_id=WEBLAUD_ID, offer_version=1, variant_ids=["custom_software"])
    ctx = admin.get(f"/platform/sales/attempts/{attempt_id}/context").json()
    sheet = ctx["cheat_sheet"]
    assert ctx["lead"]["phone"] == AUSTIN and ctx["research"]["data"]["summary"] == "Plumbers in Austin"
    assert sheet["company"] == "WebLaud" and "[your name]" in sheet["opener"] and "Joe's Plumbing" in sheet["opener"]
    assert sheet["objections"] and "eighteen and a half thousand" in sheet["pricing"]


async def test_wrap_up_before_the_call_record_closes_sets_the_outcome(admin, seeded, frozen_api_clock):
    attempt_id = await _human_call(seeded)
    callback = (NOW + timedelta(days=2)).isoformat()
    r = admin.post(f"/platform/sales/attempts/{attempt_id}/wrap-up",
                   json={"outcome": "callback", "callback_at": callback, "notes": "Owner back Thursday",
                         "task": {"title": "Prepare a quote"}, "stage": "interested"})
    assert r.status_code == 200, r.text
    # the agent closes the call afterwards with what it could tell: the person's verdict wins
    await outcomes.finish_attempt(seeded, attempt_id, "no_conversation", now=NOW, duration_s=95)
    attempt = await seeded[m.ATTEMPTS].find_one({"_id": attempt_id})
    lead = await seeded[m.LEADS].find_one({})
    assert attempt["outcome"] == "callback" and lead["status"] == "callback"
    assert outcomes._aware(lead["next_eligible_at"]) == NOW + timedelta(days=2) and lead["stage"] == "interested"
    assert await seeded[followup.TASKS].count_documents({"source": "wrapup"}) == 1
    assert "Owner back Thursday" in [a["text"] for a in await followup.activity(seeded, lead["_id"])]


async def test_wrap_up_after_the_call_record_closed_corrects_it(admin, seeded, frozen_api_clock):
    attempt_id = await _human_call(seeded)
    await outcomes.finish_attempt(seeded, attempt_id, "no_conversation", now=NOW, duration_s=60)
    r = admin.post(f"/platform/sales/attempts/{attempt_id}/wrap-up", json={"outcome": "not_interested", "notes": "Has an agency"})
    assert r.status_code == 200
    lead = await seeded[m.LEADS].find_one({})
    assert lead["status"] == "not_interested" and lead["declined_offers"] == ["off_laudline"]


async def test_wrap_up_do_not_call_needs_confirming_and_is_immediate(admin, seeded, frozen_api_clock):
    attempt_id = await _human_call(seeded)
    assert admin.post(f"/platform/sales/attempts/{attempt_id}/wrap-up", json={"outcome": "dnc"}).status_code == 422
    assert admin.post(f"/platform/sales/attempts/{attempt_id}/wrap-up", json={"outcome": "blocked_by_gate"}).status_code == 422
    assert admin.post(f"/platform/sales/attempts/{attempt_id}/wrap-up", json={"outcome": "callback"}).status_code == 422
    assert admin.post(f"/platform/sales/attempts/{attempt_id}/wrap-up",
                      json={"outcome": "dnc", "confirm_dnc": True}).status_code == 200
    assert await registry.on_internal_dnc(seeded, AUSTIN)                      # before the call record even closes
    await outcomes.finish_attempt(seeded, attempt_id, "no_conversation", now=NOW)
    assert (await seeded[m.ATTEMPTS].find_one({"_id": attempt_id}))["outcome"] == "dnc"

