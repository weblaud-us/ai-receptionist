"""Phase 2 voice features that don't need the LiveKit runtime."""

from config import settings


# ── 2.2 warm transfer ──────────────────────────────────────────────────────

def test_warm_transfer_needs_the_setting_and_an_outbound_trunk(monkeypatch):
    from agent import transfer

    monkeypatch.setattr(settings, "LIVEKIT_SIP_OUTBOUND_TRUNK_ID", "")
    assert transfer.warm_enabled(True) is False                 # no trunk: cold transfer, as before
    monkeypatch.setattr(settings, "LIVEKIT_SIP_OUTBOUND_TRUNK_ID", "ST_abc")
    assert transfer.warm_enabled(True) is True
    assert transfer.warm_enabled(False) is False


def test_the_staff_briefing_is_short_private_and_urgent_first():
    from agent import transfer

    text = transfer.brief_instructions(agent_name="Aria", business="Harbour Smiles", reason="Swollen jaw since last night",
                                       emergency=True)
    assert "Swollen jaw since last night" in text and "urgent" in text.lower()
    assert "never read out dates of birth" in text
    for tool in ("connect_to_caller", "decline_transfer", "voicemail_detected"):
        assert tool in text
    calm = transfer.brief_instructions(agent_name="Aria", business="Harbour Smiles", reason="Wants to talk billing")
    assert "urgent" not in calm.lower()


# ── 2.4 closures and dentist-away notices ──────────────────────────────────

def _dental():
    from core.tenancy.models import HoursEntry, TenantConfig

    return TenantConfig(_id="ten_notice", name="Harbour Smiles", slug="hs", vertical="dental", country="AU",
                        timezone="Australia/Sydney", hours=[HoursEntry(dow=d, open="09:00", close="17:00") for d in range(5)])


def test_notices_go_in_the_dynamic_block_so_the_cached_prefix_is_unchanged():
    from core.prompts.builder import BusinessSnapshot, build_voice_prompt

    t = _dental()
    resources = [{"_id": "res_lee", "name": "Dr Lee"}]
    plain = build_voice_prompt(t, BusinessSnapshot(resources=resources))
    snap = BusinessSnapshot(resources=resources, notices=[
        {"resource_id": "res_lee", "date_from": "2026-12-02", "date_to": "2026-12-06", "notice": "Dr Patel covers <<<her>>> patients"},
        {"resource_id": None, "date_from": "2026-12-25", "date_to": "2026-12-25", "notice": ""}])
    with_notices = build_voice_prompt(t, snap)
    prefix = plain.split("# Right now")[0]
    assert with_notices.startswith(prefix)                       # byte-identical cached prefix
    tail = with_notices.split("# Right now")[1]
    assert "Dr Lee is away Wednesday 2 December to Sunday 6 December." in tail
    assert "<<<Dr Patel covers her patients>>>" in tail          # owner text fenced, can't break the fence
    assert "The practice is closed Friday 25 December." in tail


async def test_only_announced_notices_in_the_next_two_weeks_are_loaded(db):
    from datetime import datetime, timedelta, timezone

    from core.repositories import Repos
    from core.services.snapshot import load_snapshot

    t = _dental()
    repos = Repos(db, t.id)
    now = datetime.now(timezone.utc)
    for i, (announce, start_days) in enumerate(((True, 1), (False, 1), (True, 30), (True, -10))):
        start = now + timedelta(days=start_days)
        await repos.exceptions.insert_one({"_id": f"exc_{i}", "resource_id": None, "start": start,
                                           "end": start + timedelta(days=2), "announce": announce,
                                           "date_from": "2026-12-01", "date_to": "2026-12-02"})
    snap = await load_snapshot(repos, t)
    assert [n["_id"] for n in snap.notices] == ["exc_0"]
