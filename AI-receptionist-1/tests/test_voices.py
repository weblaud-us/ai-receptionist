"""Voices per provider and country: Cartesia first for new AU / UK practices, Aura as the local-accent
fallback, and a voice id only ever sent to its own provider."""

from config import settings
from core.services.tenant_admin import create_tenant
from core.tenancy.models import TenantConfig
from core.voices import (AURA_DEFAULT_FOR_COUNTRY, AURA_VOICES, FLUX_VOICES, aura_catalogue, default_voice,
                         is_aura_model, is_flux_tts, voice_for)

EL_ID = "EST9Ui6982FZPSi7gCHi"

PASSWORD = "correct horse battery"


def test_catalogue_has_local_accents_for_both_launch_countries():
    accents = {v["voice_id"]: v["accent"] for v in AURA_VOICES}
    assert accents[AURA_DEFAULT_FOR_COUNTRY["AU"]] == "Australian"
    assert accents[AURA_DEFAULT_FOR_COUNTRY["GB"]] == "British"
    assert all(is_aura_model(v["voice_id"]) for v in AURA_VOICES)
    assert all(v["preview_url"] is None for v in aura_catalogue())
    assert not is_aura_model("EST9Ui6982FZPSi7gCHi")            # an ElevenLabs id


def test_flux_voices_are_deepgram_voices_but_flux_stt_models_are_not():
    assert all(is_flux_tts(v["voice_id"]) and is_aura_model(v["voice_id"]) for v in FLUX_VOICES)
    assert not is_flux_tts("flux-general-en") and not is_aura_model("flux-general-en")
    assert aura_catalogue()[0]["category"] == "flux"             # Flux listed first in the picker


def test_deepgram_is_a_valid_business_voice():
    t = TenantConfig.from_doc({"_id": "t", "name": "x", "slug": "x",
                               "agent": {"voice_provider": "deepgram", "voice_id": "aura-2-draco-en"}})
    assert t.agent.voice_provider == "deepgram"


def test_default_voice_is_accent_matched_per_provider(monkeypatch):
    monkeypatch.setattr(settings, "ELEVENLABS_VOICE_ID", "el-platform")
    monkeypatch.setattr(settings, "ELEVENLABS_VOICE_ID_AU", "el-au")
    monkeypatch.setattr(settings, "ELEVENLABS_VOICE_ID_GB", "")
    monkeypatch.setattr(settings, "CARTESIA_VOICE_ID", "")
    monkeypatch.setattr(settings, "CARTESIA_VOICE_ID_GB", "ca-gb")
    monkeypatch.setattr(settings, "DEEPGRAM_TTS_MODEL", "flux-hannah-en")
    monkeypatch.setattr(settings, "DEEPGRAM_TTS_MODEL_AU", "")
    monkeypatch.setattr(settings, "DEEPGRAM_TTS_MODEL_GB", "flux-kit-en")
    assert default_voice("elevenlabs", "au") == "el-au"
    assert default_voice("elevenlabs", "GB") == "el-platform"          # no UK pick yet: the platform voice
    assert default_voice("cartesia", "GB") == "ca-gb"
    assert default_voice("cartesia", "AU") == ""                       # Cartesia's own default
    assert default_voice("deepgram", "AU") == "flux-hannah-en"   # no AU pick: the platform's Deepgram voice
    assert default_voice("deepgram", "GB") == "flux-kit-en"
    assert default_voice("deepgram", "US") == "flux-hannah-en"
    assert default_voice("kokoro", "AU") == ""


def test_a_voice_id_only_reaches_its_own_provider(monkeypatch):
    monkeypatch.setattr(settings, "ELEVENLABS_VOICE_ID_AU", "el-au")
    monkeypatch.setattr(settings, "CARTESIA_VOICE_ID_AU", "ca-au")
    monkeypatch.setattr(settings, "DEEPGRAM_TTS_MODEL", "flux-hannah-en")
    monkeypatch.setattr(settings, "DEEPGRAM_TTS_MODEL_AU", "")
    assert voice_for("deepgram", "deepgram", "flux-colin-en", "AU") == "flux-colin-en"   # a Flux pick is kept
    # The practice picked an Aura voice: ElevenLabs and Cartesia fall back to their AU defaults.
    assert voice_for("deepgram", "deepgram", "aura-2-draco-en", "AU") == "aura-2-draco-en"
    assert voice_for("elevenlabs", "deepgram", "aura-2-draco-en", "AU") == "el-au"
    assert voice_for("cartesia", "deepgram", "aura-2-draco-en", "AU") == "ca-au"
    # The practice picked an ElevenLabs voice: Deepgram gets its default, not the ElevenLabs id.
    assert voice_for("elevenlabs", "elevenlabs", EL_ID, "AU") == EL_ID
    assert voice_for("deepgram", "elevenlabs", EL_ID, "AU") == "flux-hannah-en"
    assert voice_for("cartesia", "elevenlabs", EL_ID, "AU") == "ca-au"
    # Stale data: an Aura name stored under ElevenLabs is never sent to ElevenLabs.
    assert voice_for("elevenlabs", "elevenlabs", "aura-2-theia-en", "AU") == "el-au"
    assert voice_for("elevenlabs", "elevenlabs", "", "AU") == "el-au"


async def test_new_dental_practices_start_on_cartesia_when_it_is_set_up(db, monkeypatch):
    monkeypatch.setattr(settings, "CARTESIA_API_KEY", "ca-test")
    monkeypatch.setattr(settings, "ELEVENLABS_API_KEY", "el-test")              # not the platform's voice
    au = await create_tenant(name="Harbour Smiles", vertical="dental", country="AU", timezone="Australia/Sydney",
                             tenant_id="ten_au_ca", seed_template=False)
    assert (au.agent.voice_provider, au.agent.voice_id) == ("cartesia", "")     # follows CARTESIA_VOICE_ID_AU


async def test_new_dental_practices_without_cartesia_follow_the_platform_deepgram_voice(db):
    au = await create_tenant(name="Harbour Smiles", vertical="dental", country="AU", timezone="Australia/Sydney",
                             tenant_id="ten_au_voice", seed_template=False)
    gb = await create_tenant(name="Thames Dental", vertical="dental", country="GB", timezone="Europe/London",
                             tenant_id="ten_gb_voice", seed_template=False)
    salon = await create_tenant(name="Salon", vertical="salon", tenant_id="ten_salon_voice", seed_template=False)
    assert (au.agent.voice_provider, au.agent.voice_id) == ("deepgram", "")   # → DEEPGRAM_TTS_MODEL[_AU]
    assert (gb.agent.voice_provider, gb.agent.voice_id) == ("deepgram", "")
    assert au.agent.language == "en-AU"                         # the locale defaults still apply
    assert salon.agent.voice_provider == "cartesia"             # other verticals: the platform's voice


async def test_voices_endpoint_lists_flux_and_aura(db, client, monkeypatch):
    from core.auth import service as auth_service

    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    monkeypatch.setattr(settings, "DEEPGRAM_API_KEY", "dg-test")
    monkeypatch.setattr(settings, "DEEPGRAM_TTS_MODEL", "flux-hannah-en")
    monkeypatch.setattr(settings, "DEEPGRAM_TTS_MODEL_GB", "")
    auth_service.reset_rate_limits()
    await create_tenant(name="Thames Dental", vertical="dental", country="GB", timezone="Europe/London",
                        tenant_id="ten_voice_api", seed_template=False)
    await auth_service.create_user(email="owner@td.uk", password=PASSWORD, tenant_id="ten_voice_api", role="owner")
    token = client.post("/auth/login", json={"email": "owner@td.uk", "password": PASSWORD}).json()
    h = {"Authorization": f"Bearer {token['access_token']}"}
    r = client.get("/t/voices", params={"provider": "deepgram"}, headers=h).json()
    assert r["available"] and r["default_voice_id"] == "flux-hannah-en"
    assert {"flux-hannah-en", "aura-2-pandora-en", "aura-2-theia-en"} <= {v["voice_id"] for v in r["voices"]}


async def test_voices_endpoint_lists_cartesia_with_the_country_default(db, client, monkeypatch):
    from core.auth import service as auth_service
    from core.integrations import cartesia

    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    auth_service.reset_rate_limits()
    await create_tenant(name="Harbour Smiles", vertical="dental", country="AU", timezone="Australia/Sydney",
                        tenant_id="ten_ca_api", seed_template=False)
    await auth_service.create_user(email="owner@hs.au", password=PASSWORD, tenant_id="ten_ca_api", role="owner")
    token = client.post("/auth/login", json={"email": "owner@hs.au", "password": PASSWORD}).json()
    h = {"Authorization": f"Bearer {token['access_token']}"}

    monkeypatch.setattr(settings, "CARTESIA_API_KEY", "")
    assert client.get("/t/voices", params={"provider": "cartesia"}, headers=h).json()["available"] is False

    monkeypatch.setattr(settings, "CARTESIA_API_KEY", "ca-test")
    monkeypatch.setattr(settings, "CARTESIA_VOICE_ID_AU", "ca-au")

    async def fake_list(**_):
        return [{"voice_id": "ca-au", "name": "Sydney", "accent": "Australian", "preview_url": None}]
    monkeypatch.setattr(cartesia, "list_voices", fake_list)
    r = client.get("/t/voices", params={"provider": "cartesia"}, headers=h).json()
    assert r["available"] and r["default_voice_id"] == "ca-au" and r["voices"][0]["accent"] == "Australian"
