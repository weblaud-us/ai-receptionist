"""scripts/seed_web_demo.py: two fictional, always-open demo practices with no alerts; re-runs reset them."""

from config import settings
from core.repositories import Repos
from core.tenancy import resolve_by_id
from scripts.seed_web_demo import seed


async def test_seed_creates_both_demo_practices(db):
    assert await seed(db, max_concurrent=4) == ["ten_demo_au", "ten_demo_gb"]
    au, gb = await resolve_by_id("ten_demo_au"), await resolve_by_id("ten_demo_gb")
    assert (au.name, au.country, au.currency, au.vertical) == ("Harbourline Dental", "AU", "AUD", "dental")
    assert (gb.name, gb.country, gb.timezone) == ("Wimpole Dental", "GB", "Europe/London")
    for t in (au, gb):
        assert t.status == "active" and len(t.hours) == 7 and t.limits.max_concurrent_calls == 4
        assert t.agent.name == "Ava" and t.agent.max_call_minutes * 60 >= settings.WEB_DEMO_MAX_SECONDS
        assert not t.notifications.owner_email and not t.notifications.daily_digest
        assert not t.missed_call_recovery.enabled and not t.escalation.enabled
        assert "demo" in t.agent.custom_instructions.lower()
    assert au.agent.language == "en-AU" and gb.agent.stt_language == "en-GB"
    assert {r["name"] for r in await Repos(db, "ten_demo_au").resources.find({})} == {"Dr Priya Patel", "Dr Tom Nguyen"}
    assert await Repos(db, "ten_demo_au").services.count({}) > 0           # the dental template


async def test_a_rerun_resets_demo_settings_but_keeps_a_chosen_voice(db):
    await seed(db)
    await db.tenants.update_one({"_id": "ten_demo_au"}, {"$set": {"agent.voice_id": "el-chosen", "hours": [],
                                                                  "agent.name": "Zed"}})
    await seed(db)
    au = await db.tenants.find_one({"_id": "ten_demo_au"})
    assert au["agent"]["voice_id"] == "el-chosen" and au["agent"]["name"] == "Ava" and len(au["hours"]) == 7
    assert await Repos(db, "ten_demo_au").resources.count({}) == 2                # not duplicated
