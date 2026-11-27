# ruff: noqa: F811  (pytest fixtures shared with other test modules are imported, then requested by name)
"""SALES_LEARNING_PLAN.md: the demo data and the replay script — idempotent, never dialable, read-only."""

from datetime import datetime, timezone

from core.outbound import dialer, learning
from core.outbound import models as m
from core.outbound import playbook as pb
from core.outbound.offer_seeds import WEBLAUD_ID
from scripts import learning_replay, seed_learning_demo as demo
from tests.test_sales_playbook import offers_db  # noqa: F401

NOW = datetime(2026, 12, 1, 3, 0, tzinfo=timezone.utc)


async def test_learning_demo_seed_is_idempotent_and_never_dialable(offers_db):
    db = offers_db
    dry = await demo.seed(db, now=NOW, apply=False)
    assert dry["calls"] > 300 and await db[m.ATTEMPTS].count_documents({}) == 0          # a dry run writes nothing
    first = await demo.seed(db, now=NOW)
    second = await demo.seed(db, now=NOW)
    assert first == second and await db[m.ATTEMPTS].count_documents({"demo": True}) == first["calls"]
    assert await dialer.claim_lead(db, demo.CAMPAIGN_ID, NOW) is None                      # paused, final, owner "demo"
    assert not await db[m.LEADS].count_documents({"demo": True, "owner": m.OWNER_PLATFORM})
    page_state = await pb.learning_doc(db, WEBLAUD_ID)
    assert page_state["live_playbook_id"] == f"{WEBLAUD_ID}@pb1" and page_state["experiment_id"] == demo.EXPERIMENT_ID
    curve = await learning.learning_curve(db, WEBLAUD_ID, now=NOW)
    assert curve["points"] and any(mk["kind"] == "rolled_back" for mk in curve["marks"])
    assert await db["sales_learning_stats"].find_one({"_id": "timing:AU"})
    log = await demo.fast_forward(db, now=NOW, days=40, seed=7, rates=(0.18, 0.27))
    assert log and log[-1].split(": ", 1)[1].split(" —")[0] in ("running", "promoted", "rolled_back", "inconclusive")
    replay = await learning_replay.replay(db, WEBLAUD_ID)
    assert replay[0].startswith(f"Test {demo.EXPERIMENT_ID}")
    removed = await demo.remove(db)
    assert removed[m.ATTEMPTS] >= first["calls"] and await db[m.ATTEMPTS].count_documents({}) == 0
    assert await db[pb.PLAYBOOKS].count_documents({}) == 0
    assert (await pb.learning_doc(db, WEBLAUD_ID))["live_playbook_id"] is None
