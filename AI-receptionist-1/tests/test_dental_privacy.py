"""Dental Phase 4: health-data redaction, clinical text encrypted at rest, retention, access and erasure."""

from datetime import datetime, timedelta, timezone

import pytest

from core import crypto
from core.compliance import subjects
from core.log import medicare_number_valid, nhs_number_valid, redact, redact_obj
from core.repositories import Repos
from core.sensitive import drop_ciphertext, seal, unseal
from core.services.tenant_admin import create_tenant
from core.tenancy import invalidate

CALLER = "+61412345678"
NOW = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fresh():
    invalidate()
    yield
    invalidate()


# ── Redaction ────────────────────────────────────────────

def test_health_identifiers_and_dates_of_birth_are_masked():
    assert nhs_number_valid("9434765919") and not nhs_number_valid("9434765918")
    assert medicare_number_valid("2123456701") and not medicare_number_valid("2123456791")
    out = redact("NHS 943 476 5919, Medicare 2123 45670 1, DOB: 1987-03-14, born 14/03/1987")
    assert "943 476 5919" not in out and "[NHS number]" in out
    assert "2123 45670 1" not in out and "[Medicare number]" in out
    assert "1987" not in out
    # Booking dates and IDs in our own logs stay readable; numbers that fail the check digit stay too.
    assert redact("booking HTL1A2B3C4D on 2026-11-14") == "booking HTL1A2B3C4D on 2026-11-14"
    assert "123 456 7890" in redact("ref 123 456 7890")


def test_sentry_events_lose_free_text_health_details():
    from core.observability import _before_send

    event = {"message": "Tool failed", "extra": {"args": {"symptom_summary": "abscess, swollen jaw",
                                                          "notes": "anxious patient", "kind": "urgent"}},
             "breadcrumbs": [{"data": {"transcript": [{"text": "my face is swollen"}]}}]}
    out = _before_send(event, None)
    assert out["extra"]["args"]["symptom_summary"] == "[redacted]" and out["extra"]["args"]["notes"] == "[redacted]"
    assert out["extra"]["args"]["kind"] == "urgent" and out["message"] == "Tool failed"
    assert "swollen" not in str(out)
    assert redact_obj({"summary": ""}) == {"summary": ""}                     # empty values stay empty


# ── Sealing ──────────────────────────────────────────────

def test_seal_and_unseal_round_trip_and_ciphertext_never_leaves():
    doc = {"_id": "x", "problem": "toothache", "notes": "", "transcript": [{"role": "user", "text": "it hurts"}]}
    sealed = seal(doc)
    assert "problem" not in sealed and "transcript" not in sealed and "notes" not in sealed
    assert "toothache" not in str(sealed) and "it hurts" not in str(sealed)
    assert unseal(sealed) == {"_id": "x", "problem": "toothache", "transcript": [{"role": "user", "text": "it hurts"}]}
    assert drop_ciphertext(sealed) == {"_id": "x"}
    broken = {**sealed, "problem_enc": "not-a-token"}
    assert unseal(broken)["problem"] is None                                  # wrong key: unreadable, not a crash
    crypto.reset_cache()


async def test_dental_businesses_encrypt_by_default_and_keep_leads_a_year(db):
    t = await create_tenant(name="Harbour Smiles", vertical="dental", country="AU", tenant_id="ten_p1",
                            seed_template=False)
    assert t.clinical_encryption and t.privacy.lead_retention_days == 365
    salon = await create_tenant(name="Bella Salon", vertical="salon", tenant_id="ten_p2", seed_template=False)
    assert not salon.clinical_encryption and salon.privacy.lead_retention_days == 0


async def test_dental_call_transcripts_are_written_encrypted(db):
    from agent.persistence import CallRecorder
    from agent.profile import VoiceProfile
    from agent.state import CallState

    dental = await create_tenant(name="Harbour Smiles", vertical="dental", country="AU", tenant_id="ten_tx",
                                 seed_template=False)
    salon = await create_tenant(name="Bella Salon", vertical="salon", tenant_id="ten_tx2", seed_template=False)
    for t in (dental, salon):
        state = CallState(call_id=f"call_{t.id}", profile=VoiceProfile(tenant=t), caller_phone=CALLER)
        state.transcript.append({"role": "user", "text": "my gum is bleeding", "at": NOW})
        await CallRecorder(state)._write(final=True)
    sealed = await Repos(db, dental.id).calls.find_one({"call_id": "call_ten_tx"})
    assert "transcript" not in sealed and "bleeding" not in str(sealed)
    assert unseal(sealed)["transcript"][0]["text"] == "my gum is bleeding"
    plain = await Repos(db, salon.id).calls.find_one({"call_id": "call_ten_tx2"})
    assert plain["transcript"][0]["text"] == "my gum is bleeding"


# ── API, access and erasure ──────────────────────────────

async def _dental_with_data(db, tenant_id="ten_dsr"):
    t = await create_tenant(name="Harbour Smiles", vertical="dental", country="AU", tenant_id=tenant_id,
                            seed_template=False, status="active")
    repos = Repos(db, t.id)
    await repos.calls.insert_one(seal({"call_id": f"call_{tenant_id}", "status": "completed", "caller_phone": CALLER,
                                       "started_at": NOW - timedelta(days=200), "ended_at": NOW - timedelta(days=200),
                                       "transcript": [{"role": "user", "text": "abscess"}]}, ("transcript",)))
    await repos.leads.insert_one(seal({"_id": f"lead_{tenant_id}", "kind": "dental_emergency", "phone": CALLER,
                                       "name": "Sam", "problem": "abscess", "status": "new",
                                       "created_at": NOW - timedelta(days=400)}))
    return t


async def test_api_shows_decrypted_text_and_never_ciphertext(db, client, monkeypatch):
    from config import settings
    from core.auth import service as auth_service

    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    auth_service.reset_rate_limits()
    await _dental_with_data(db)
    await auth_service.create_user(email="owner@hs.au", password="correct horse battery", tenant_id="ten_dsr",
                                   role="owner")
    token = client.post("/auth/login", json={"email": "owner@hs.au", "password": "correct horse battery"}).json()
    h = {"Authorization": f"Bearer {token['access_token']}"}
    detail = client.get("/t/calls/call_ten_dsr", headers=h).json()
    assert detail["transcript"][0]["text"] == "abscess" and "transcript_enc" not in detail
    listing = client.get("/t/calls", headers=h).text
    assert "transcript_enc" not in listing and "gAAAA" not in listing          # no Fernet tokens
    leads = client.get("/t/leads", headers=h).json()["leads"]
    assert leads[0]["problem"] == "abscess" and "problem_enc" not in leads[0]
    admin = client.get("/admin/calls/call_ten_dsr", headers=h).json()
    assert admin["transcript"][0]["text"] == "abscess" and "transcript_enc" not in admin


async def test_access_request_is_readable_and_erasure_removes_encrypted_text(db):
    t = await _dental_with_data(db)
    data = await subjects.export(db, t, CALLER)
    assert data["data"]["leads"][0]["problem"] == "abscess"
    assert data["data"]["calls"][0]["transcript"][0]["text"] == "abscess"
    assert "_enc" not in str(data)
    await subjects.erase(db, t, CALLER, actor="usr_1")
    call = await Repos(db, t.id).calls.find_one({"call_id": "call_ten_dsr"})
    assert "transcript_enc" not in call and "transcript" not in call
    assert await Repos(db, t.id).leads.count({}) == 0


async def test_retention_purges_encrypted_transcripts_and_old_leads(db):
    from services import tenant_jobs

    t = await _dental_with_data(db)
    assert await tenant_jobs.purge_transcripts(t, NOW) == 1                   # older than 90 days
    call = await Repos(db, t.id).calls.find_one({"call_id": "call_ten_dsr"})
    assert "transcript_enc" not in call and call["transcript_purged_at"]
    assert await tenant_jobs.purge_leads(t, NOW) == 1                         # older than 365 days
    salon = await create_tenant(name="Bella Salon", vertical="salon", tenant_id="ten_keep", seed_template=False)
    await Repos(db, salon.id).leads.insert_one({"_id": "lead_old", "phone": CALLER, "created_at": NOW - timedelta(days=900)})
    assert await tenant_jobs.purge_leads(salon, NOW) == 0                     # 0 = keep


# ── Migration ────────────────────────────────────────────

async def test_migration_encrypts_old_plaintext_for_dental_only_and_is_idempotent(db):
    from migrations import m0005_encrypt_clinical_text as m0005

    dental = await create_tenant(name="Harbour Smiles", vertical="dental", country="AU", tenant_id="ten_m1",
                                 seed_template=False)
    salon = await create_tenant(name="Bella Salon", vertical="salon", tenant_id="ten_m2", seed_template=False)
    for t in (dental, salon):
        repos = Repos(db, t.id)
        await repos.leads.insert_one({"_id": f"lead_{t.id}", "phone": CALLER, "problem": "cracked tooth", "notes": "n"})
        await repos.calls.insert_one({"call_id": f"call_{t.id}", "transcript": [{"role": "user", "text": "ow"}]})
    dry = await m0005.run(db, apply=False)
    assert dry["leads_found"] == 1 and dry["calls_found"] == 1 and dry["leads_sealed"] == 0
    applied = await m0005.run(db, apply=True)
    assert applied["leads_sealed"] == 1 and applied["calls_sealed"] == 1
    lead = await Repos(db, dental.id).leads.find_one({"_id": "lead_ten_m1"})
    assert "problem" not in lead and unseal(lead)["problem"] == "cracked tooth" and unseal(lead)["notes"] == "n"
    assert (await Repos(db, salon.id).leads.find_one({"_id": "lead_ten_m2"}))["problem"] == "cracked tooth"
    again = await m0005.run(db, apply=True)
    assert again["leads_found"] == 0 and again["calls_found"] == 0


# ── Audit PRIV-08: call summaries retell what the caller said ──────────────

async def test_dental_call_summaries_are_sealed_but_still_read_where_needed(db, client, monkeypatch):
    from agent import persistence
    from agent.persistence import CallRecorder, load_contact
    from agent.profile import VoiceProfile
    from agent.state import CallState
    from config import settings
    from core.auth import service as auth_service
    from migrations import m0005_encrypt_clinical_text as m0005

    async def fake_summary(*a, **k):
        return "Sam has a swollen gum and wants an urgent visit."

    monkeypatch.setattr(persistence, "summarise_call", fake_summary)
    t = await create_tenant(name="Harbour Smiles", vertical="dental", country="AU", tenant_id="ten_sum",
                            seed_template=False, status="active")
    state = CallState(call_id="call_sum", profile=VoiceProfile(tenant=t), caller_phone=CALLER)
    await CallRecorder(state)._write(final=True)
    await CallRecorder(state)._summarise_and_notify()
    raw = await Repos(db, t.id).calls.find_one({"call_id": "call_sum"})
    assert "summary" not in raw and "swollen" not in str(raw) and raw["summary_enc"]

    later = CallState(call_id="call_next", profile=VoiceProfile(tenant=t), caller_phone=CALLER)
    history = await load_contact(later)
    assert "swollen gum" in " ".join(history["recent_calls"])          # the assistant still remembers

    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    auth_service.reset_rate_limits()
    await auth_service.create_user(email="o@sum.au", password="correct horse battery", tenant_id=t.id, role="owner")
    tok = client.post("/auth/login", json={"email": "o@sum.au", "password": "correct horse battery"}).json()
    listing = client.get("/t/calls", headers={"Authorization": f"Bearer {tok['access_token']}"}).json()["calls"]
    assert listing[0]["summary"].startswith("Sam has a swollen gum") and "summary_enc" not in listing[0]

    await Repos(db, t.id).calls.insert_one({"call_id": "call_old", "summary": "old plain summary"})
    await m0005.run(db, apply=True)
    old = await Repos(db, t.id).calls.find_one({"call_id": "call_old"})
    assert "summary" not in old and unseal(old)["summary"] == "old plain summary"
