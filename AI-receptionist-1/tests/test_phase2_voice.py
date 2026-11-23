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


# ── 2.6 safer speech ───────────────────────────────────────────────────────

def _speak(chunks):
    from agent.speech import ClinicalGuard

    hits: list = []
    g = ClinicalGuard(hits.append)
    out = "".join(g.push(c) for c in chunks) + g.flush()
    return out, hits


def test_medicine_and_dose_advice_never_reaches_the_voice():
    from agent.speech import SAFE_LINE

    out, hits = _speak(["Sorry to hear that. ", "You could ", "take 400 ", "mg of ibu", "profen every ", "six hours. ",
                        "Shall I book you in?"])
    assert "ibuprofen" not in out and "400" not in out and "mg" not in out
    assert out.startswith("Sorry to hear that.") and SAFE_LINE.strip() in out and out.endswith("Shall I book you in?")
    assert hits


def test_guard_catches_phrases_split_token_by_token():
    text = "Thanks Sam. You have probably got an abscess, so let's see you today. Is 2 p.m. okay?"
    tokens = [t for t in text.replace(" ", " \0").split("\0")]      # one word per chunk, like an LLM
    out, hits = _speak(tokens)
    assert "abscess" not in out and hits and out.startswith("Thanks Sam.") and out.endswith("Is 2 p.m. okay?")


def test_ordinary_words_are_not_held_back():
    from agent.speech import ClinicalGuard

    g = ClinicalGuard()
    assert g.push("Great, I've booked ") == "Great, I've booked "            # released at once
    assert g.push("Dr Lee") == "Dr "                                            # "Lee" may still be growing
    assert g.push(" for ") == "Lee for "


def test_diagnosis_language_is_replaced_but_normal_talk_passes_untouched():
    out, hits = _speak(["That sounds like ", "an abscess to me. ", "Let's get you seen today."])
    assert "abscess" not in out and out.endswith("Let's get you seen today.") and hits
    normal = ["I've got Tuesday at 10 30 a.m. with Dr Lee. ", "Would that work for you?"]
    out, hits = _speak(normal)
    assert out == "".join(normal) and not hits


def test_dental_implant_and_cosmetic_prices_are_always_from_prices():
    from core.prompts.builder import price_text

    implant = {"name": "Single implant", "category": "implant", "price": {"display": "$4,500"}}
    assert price_text(implant, "dental") == "from $4,500, exact quote after an examination"
    already = {"category": "cosmetic", "price": {"display": "From $650 per tooth"}}
    assert price_text(already, "dental") == "From $650 per tooth, exact quote after an examination"
    exam = {"category": "routine", "price": {"display": "$150"}}
    assert price_text(exam, "dental") == "$150"                         # fixed fees stay as they are
    assert price_text(implant, "salon") == "$4,500"


def test_the_code_default_voice_matches_the_au_uk_setup():
    # Cartesia Sonic-3 is the platform's voice; Deepgram Aura (same key as the STT) is the fallback.
    fields = type(settings).model_fields
    assert fields["VOICE_TTS_PROVIDER"].default == "cartesia"
    assert fields["CARTESIA_MODEL"].default == "sonic-3"
    assert fields["VOICE_TTS_FALLBACKS"].default == "deepgram"


# ── 2.7 callers in other languages ─────────────────────────────────────────

def test_language_rule_follows_the_voice():
    from core.prompts.builder import build_chat_prompt, build_voice_prompt

    t = _dental()
    assert "# Languages" not in build_voice_prompt(t)                       # English-only STT: no rule
    t.agent.stt_language = "multi"
    t.agent.voice_provider = "elevenlabs"
    assert "Answer in the language they use" in build_voice_prompt(t)
    t.agent.voice_provider = "deepgram"
    english_only = build_voice_prompt(t)
    assert "can only speak English" in english_only and "take_message" in english_only.split("# Languages")[1]
    assert "# Languages" not in build_chat_prompt(t)                        # WhatsApp text has no voice limit
