"""Plan A2.5: Deepgram Aura as a per-business voice, defaulting to a local accent for AU / UK practices."""

from config import settings
from core.services.tenant_admin import create_tenant
from core.tenancy.models import TenantConfig
from core.voices import AURA_DEFAULT_FOR_COUNTRY, AURA_VOICES, aura_catalogue, is_aura_model

PASSWORD = "correct horse battery"


def test_catalogue_has_local_accents_for_both_launch_countries():
    accents = {v["voice_id"]: v["accent"] for v in AURA_VOICES}
    assert accents[AURA_DEFAULT_FOR_COUNTRY["AU"]] == "Australian"
    assert accents[AURA_DEFAULT_FOR_COUNTRY["GB"]] == "British"
    assert all(is_aura_model(v["voice_id"]) for v in AURA_VOICES)
    assert all(v["preview_url"] is None for v in aura_catalogue())
    assert not is_aura_model("EST9Ui6982FZPSi7gCHi")            # an ElevenLabs id


def test_deepgram_is_a_valid_business_voice():
    t = TenantConfig.from_doc({"_id": "t", "name": "x", "slug": "x",
                               "agent": {"voice_provider": "deepgram", "voice_id": "aura-2-draco-en"}})
    assert t.agent.voice_provider == "deepgram"


async def test_new_dental_practices_start_on_an_accent_matched_aura_voice(db):
    au = await create_tenant(name="Harbour Smiles", vertical="dental", country="AU", timezone="Australia/Sydney",
                             tenant_id="ten_au_voice", seed_template=False)
    gb = await create_tenant(name="Thames Dental", vertical="dental", country="GB", timezone="Europe/London",
                             tenant_id="ten_gb_voice", seed_template=False)
    salon = await create_tenant(name="Salon", vertical="salon", tenant_id="ten_salon_voice", seed_template=False)
    assert (au.agent.voice_provider, au.agent.voice_id) == ("deepgram", "aura-2-theia-en")
    assert (gb.agent.voice_provider, gb.agent.voice_id) == ("deepgram", "aura-2-pandora-en")
    assert au.agent.language == "en-AU"                         # the locale defaults still apply
    assert salon.agent.voice_provider == "elevenlabs"           # other verticals unchanged


async def test_voices_endpoint_lists_aura(db, client, monkeypatch):
    from core.auth import service as auth_service

    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    monkeypatch.setattr(settings, "DEEPGRAM_API_KEY", "dg-test")
    auth_service.reset_rate_limits()
    await create_tenant(name="Thames Dental", vertical="dental", country="GB", timezone="Europe/London",
                        tenant_id="ten_voice_api", seed_template=False)
    await auth_service.create_user(email="owner@td.uk", password=PASSWORD, tenant_id="ten_voice_api", role="owner")
    token = client.post("/auth/login", json={"email": "owner@td.uk", "password": PASSWORD}).json()
    h = {"Authorization": f"Bearer {token['access_token']}"}
    r = client.get("/t/voices", params={"provider": "deepgram"}, headers=h).json()
    assert r["available"] and r["default_voice_id"] == "aura-2-pandora-en"
    assert {"aura-2-pandora-en", "aura-2-theia-en"} <= {v["voice_id"] for v in r["voices"]}
