# ruff: noqa: F811  (pytest fixtures shared with test_phase4_outbound are imported, then requested by name)
"""
SALES_LEARNING_PLAN.md Phase 7: calls shift toward the winning approved script — even until each has
30 businesses reached, never under 20%, never more traffic for a script that drives people off, sticky
per business, with the pick probability recorded and the legal review untouched.
"""

import random
from datetime import timedelta

from core.outbound import bandit, dialer
from core.outbound import models as m
from core.outbound.learning_stats import ArmStats
from core.outbound.offer_seeds import WEBLAUD_ID
from tests.test_phase4_outbound import NOW, Dispatched, admin, campaign_doc, frozen_api_clock, ready, seeded  # noqa: F401

IDS = ["ai_automation", "custom_software"]


def test_even_until_each_script_has_30_businesses():
    alloc, how = bandit.allocation({"ai_automation": ArmStats(leads=29, progress=20), "custom_software": ArmStats(leads=80, progress=4)}, IDS)
    assert how == "even" and alloc == {"ai_automation": 0.5, "custom_software": 0.5}


def test_moves_toward_the_better_script_but_never_below_the_floor():
    alloc, how = bandit.allocation({"ai_automation": ArmStats(leads=60, progress=24), "custom_software": ArmStats(leads=60, progress=6)}, IDS)
    assert how == "auto" and alloc["ai_automation"] == 0.8 and alloc["custom_software"] == 0.2
    close, _ = bandit.allocation({"ai_automation": ArmStats(leads=40, progress=10), "custom_software": ArmStats(leads=40, progress=9)}, IDS)
    assert 0.4 < close["ai_automation"] < 0.7


def test_a_script_that_drives_people_off_never_gains_traffic():
    stats = {"ai_automation": ArmStats(leads=60, progress=24, opener_hangups=25), "custom_software": ArmStats(leads=60, progress=12, opener_hangups=5)}
    alloc, _ = bandit.allocation(stats, IDS)
    assert alloc["ai_automation"] <= 0.5


def test_pick_follows_the_shares():
    rng = random.Random(4)
    picks = [bandit.pick({"a": 0.8, "b": 0.2}, rng) for _ in range(2000)]
    assert 0.75 < picks.count("a") / 2000 < 0.85


def _auto(**kw):
    return campaign_doc(offer_id=WEBLAUD_ID, offer_version=1, variant_ids=IDS, script_split="auto", **kw)


async def _history(db, variant, n, progress):
    for i in range(n):
        ok = i < progress
        await db[m.ATTEMPTS].insert_one({
            "_id": f"h_{variant}_{i}", "call_id": f"h-{variant}-{i}", "lead_id": f"hl_{variant}_{i}", "campaign_id": "cmp_1",
            "variant_id": variant, "status": "done", "amd": "human", "outcome": "demo_booked" if ok else "not_interested",
            "events": ["meeting"] if ok else [], "created_at": NOW - timedelta(days=2)})


async def test_dials_follow_the_split_stick_per_business_and_record_the_probability(seeded):
    db = seeded
    from migrations import m0006_sales_offers

    await m0006_sales_offers.run(db, apply=True)
    await db[m.CAMPAIGNS].replace_one({"_id": "cmp_1"}, _auto())
    await _history(db, "ai_automation", 40, 20)
    await _history(db, "custom_software", 40, 4)
    campaign = await db[m.CAMPAIGNS].find_one({"_id": "cmp_1"})
    lead = await dialer.claim_lead(db, "cmp_1", NOW)
    out = await dialer.dial_lead(db, campaign, lead, NOW, Dispatched())
    attempt = await db[m.ATTEMPTS].find_one({"_id": out["attempt_id"]})
    assert attempt["variant_pick"]["how"] == "auto" and attempt["variant_pick"]["p"] in (0.8, 0.2)
    pinned = (await db[m.LEADS].find_one({"_id": lead["_id"]}))["script_pick"]["variant_id"]
    assert pinned == attempt["variant_id"]
    again = await bandit.choose(db, campaign, await db[m.LEADS].find_one({"_id": lead["_id"]}), now=NOW)
    assert again == (pinned, {"how": "kept", "p": None})
    doc = await db[bandit.STATS].find_one({"_id": "scripts:cmp_1"})
    assert doc["alloc"]["ai_automation"] == 0.8 and doc["how"] == "auto"


async def test_human_assisted_legacy_and_even_keep_the_hash_split(seeded):
    db = seeded
    lead = await db[m.LEADS].find_one({})
    for campaign in (_auto(mode="human_assisted"), campaign_doc(), {**_auto(), "script_split": "even"},
                     {k: v for k, v in _auto().items() if k != "script_split"}):
        assert await bandit.choose(db, campaign, lead, now=NOW) is None


async def test_changing_the_split_keeps_the_legal_review_while_running(admin, seeded, frozen_api_clock):
    db = seeded
    from migrations import m0006_sales_offers

    await m0006_sales_offers.run(db, apply=True)
    await db[m.CAMPAIGNS].replace_one({"_id": "cmp_1"}, campaign_doc(offer_id=WEBLAUD_ID, offer_version=1, variant_ids=IDS))
    r = admin.patch("/platform/sales/campaigns/cmp_1", json={"script_split": "auto", "retry_timing": "smart"})
    assert r.status_code == 200, r.text
    after = await db[m.CAMPAIGNS].find_one({"_id": "cmp_1"})
    assert after["script_split"] == "auto" and after["status"] == "running" and m.legal_review_valid(after)
    split = admin.get("/platform/sales/campaigns/cmp_1/scripts").json()
    assert split["split"] == "auto" and split["scripts"]["how"] == "even"            # no history yet: warm-up
