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
