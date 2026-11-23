"""Dental vertical tools through run_tool: tool sets, emergency triage, cosmetic lead value, isolation, erasure."""

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from core.compliance import subjects
from core.notifications import drain
from core.repositories import Repos
from core.sensitive import unseal
from core.tenancy.models import HoursEntry, TenantConfig
from core.tools.dental import estimate_value
from core.tools.hotel import drain_background_tasks
from core.tools.registry import ToolContext, run_tool, tool_names_for


@pytest.fixture(autouse=True)
def _sms_on(monkeypatch):
    """These tests cover the SMS paths, which the platform has switched off (config.SMS_ENABLED)."""
    from config import settings as _settings

    monkeypatch.setattr(_settings, "SMS_ENABLED", True)


SYD = "Australia/Sydney"
LDN = "Europe/London"
CALLER = "+61412345678"


def _tenant(tenant_id="ten_dent", *, country="AU", tz=SYD, vertical="dental", **kw) -> TenantConfig:
    return TenantConfig(_id=tenant_id, name="Harbour Smiles", slug=tenant_id.replace("_", "-"), vertical=vertical,
                        country=country, timezone=tz, currency="AUD" if country == "AU" else "GBP",
                        hours=[HoursEntry(dow=d, open="09:00", close="17:00") for d in range(5)], **kw)


def _ctx(tenant, *, call_id="call-d1", now=None, phone=CALLER):
    now = now or datetime(2026, 11, 23, 10, 0, tzinfo=ZoneInfo(tenant.timezone)).astimezone(timezone.utc)
    return ToolContext(channel="voice", caller_phone=phone, call_id=call_id, tenant=tenant, now=now)


async def _call(ctx, name, args):
    return json.loads(await run_tool(ctx, name, args))


@pytest.fixture(autouse=True)
async def sent(monkeypatch):
    import core.integrations.sms as sms
    import core.integrations.whatsapp as wa

    out = []

    async def fake(*a, **k):
        out.append(a)
        return True

    monkeypatch.setattr(wa, "send_text", fake)
    monkeypatch.setattr(sms, "send_sms", fake)
    yield out
    await drain_background_tasks()
    await drain()


def _owner(**kw):
    return {"notifications": {"owner_phone": "+61400000000", "on_message": ["sms"], "on_urgent": ["sms"], **kw}}


# ── Tool sets ────────────────────────────────────────────

def test_dental_tools_only_for_dental_tenants():
    names = tool_names_for(_tenant())
    assert {"triage_dental_emergency", "qualify_cosmetic_lead", "check_availability", "take_message"} <= set(names)
    for other in ("clinic", "salon", "home_services", "hotel", "generic"):
        assert "triage_dental_emergency" not in tool_names_for(_tenant(vertical=other))
        assert "qualify_cosmetic_lead" not in tool_names_for(_tenant(vertical=other))


async def test_dental_tools_are_unknown_outside_the_vertical(db):
    r = await _call(_ctx(_tenant(vertical="clinic")), "triage_dental_emergency",
                    {"symptom_summary": "pain", "severity_level": "urgent", "swelling_or_bleeding_red_flag": False})
    assert r["error"].startswith("Unknown tool")


# ── Emergency triage ─────────────────────────────────────

@pytest.mark.parametrize("country,tz,number", [("AU", SYD, "000"), ("GB", LDN, "999")])
async def test_red_flag_returns_the_local_emergency_number_and_alerts_the_practice(db, sent, country, tz, number):
    t = _tenant(country=country, tz=tz, **_owner())
    r = await _call(_ctx(t), "triage_dental_emergency",
                    {"symptom_summary": "face swelling towards eye, hard to swallow", "severity_level": "urgent",
                     "swelling_or_bleeding_red_flag": True, "patient_name": "Sam"})
    assert r["success"] and r["escalation"] == "emergency_services" and r["emergency_number"] == number
    assert number in r["message"] and "Don't book" in r["message"]
    lead = await Repos(db, t.id).leads.find_one({"_id": r["lead_id"]})
    assert lead["kind"] == "dental_emergency" and lead["urgency"] == "emergency" and lead["red_flag"] is True
    assert lead["phone"] == CALLER and lead["severity"] == "life_threatening"
    await drain()
    assert any("EMERGENCY" in str(a[1]) for a in sent)


async def test_au_closed_practice_mentions_healthdirect(db):
    t = _tenant("ten_au_night")
    night = datetime(2026, 11, 23, 21, 0, tzinfo=ZoneInfo(SYD)).astimezone(timezone.utc)
    r = await _call(_ctx(t, now=night), "triage_dental_emergency",
                    {"symptom_summary": "bad toothache", "severity_level": "urgent", "swelling_or_bleeding_red_flag": False})
    assert "Healthdirect on 1800 022 222" in r["message"] and "111" not in r["message"]
    day = await _call(_ctx(t, call_id="call-d2"), "triage_dental_emergency",
                      {"symptom_summary": "chipped tooth", "severity_level": "urgent", "swelling_or_bleeding_red_flag": False})
    assert "Healthdirect" not in day["message"]                # open: the practice itself is the answer


async def test_urgent_problem_is_same_day_and_gb_closed_practice_mentions_nhs_111(db):
    au = await _call(_ctx(_tenant()), "triage_dental_emergency",
                     {"symptom_summary": "lost crown", "severity_level": "urgent", "swelling_or_bleeding_red_flag": False})
    assert au["escalation"] == "same_day" and "check_availability" in au["message"] and "111" not in au["message"]
    gb = _tenant("ten_gb", country="GB", tz=LDN)
    night = datetime(2026, 11, 23, 21, 0, tzinfo=ZoneInfo(LDN)).astimezone(timezone.utc)
    r = await _call(_ctx(gb, now=night), "triage_dental_emergency",
                    {"symptom_summary": "bad toothache", "severity_level": "urgent", "swelling_or_bleeding_red_flag": False})
    assert "NHS 111" in r["message"]
    lead = await Repos(db, gb.id).leads.find_one({"_id": r["lead_id"]})
    assert lead["urgency"] == "today" and "emergency_number" not in r


async def test_triage_needs_a_summary_and_a_number_unless_life_threatening(db):
    t = _tenant()
    r = await _call(_ctx(t), "triage_dental_emergency", {"symptom_summary": " ", "severity_level": "urgent",
                                                         "swelling_or_bleeding_red_flag": False})
    assert r["error"] == "symptom_summary_required"
    r = await _call(_ctx(t, phone=None), "triage_dental_emergency",
                    {"symptom_summary": "pain", "severity_level": "urgent", "swelling_or_bleeding_red_flag": False})
    assert r["error"] == "phone_required"
    r = await _call(_ctx(t, phone=None), "triage_dental_emergency",
                    {"symptom_summary": "bleeding won't stop", "severity_level": "urgent",
                     "swelling_or_bleeding_red_flag": True})
    assert r["success"] and r["emergency_number"] == "000"


async def test_a_second_call_in_the_same_phone_call_updates_the_lead(db, sent):
    t = _tenant(**_owner())
    args = {"symptom_summary": "chipped tooth", "severity_level": "urgent", "swelling_or_bleeding_red_flag": False,
            "symptom_consent": True}
    first = await _call(_ctx(t), "triage_dental_emergency", args)
    second = await _call(_ctx(t), "triage_dental_emergency", {**args, "symptom_summary": "chipped tooth, now sharp pain"})
    assert first["lead_id"] == second["lead_id"]
    leads = await Repos(db, t.id).leads.find({"kind": "dental_emergency"})
    assert len(leads) == 1 and "problem" not in leads[0] and leads[0]["problem_enc"]      # encrypted at rest
    assert unseal(leads[0])["problem"] == "chipped tooth, now sharp pain"
    await drain()
    assert len([a for a in sent if "Urgent dental problem" in str(a[1])]) == 1      # alerted once
    other_call = await _call(_ctx(t, call_id="call-d2"), "triage_dental_emergency", args)
    assert other_call["lead_id"] != first["lead_id"]


async def test_symptoms_are_kept_only_with_consent_or_in_a_red_flag_emergency(db, sent):
    # Audit P0-07: health data needs the caller's yes; a life-threatening emergency is kept regardless.
    t = _tenant("ten_consent", **_owner())
    base = {"severity_level": "urgent", "swelling_or_bleeding_red_flag": False}
    no = await _call(_ctx(t, call_id="c-no"), "triage_dental_emergency", {**base, "symptom_summary": "lost filling"})
    lead = unseal(await Repos(db, t.id).leads.find_one({"_id": no["lead_id"]}))
    assert no["success"] and not lead.get("problem") and lead["symptom_consent"] is False
    await drain()
    assert any("didn't agree" in str(a[1]) for a in sent)            # the practice still gets the callback
    red = await _call(_ctx(t, call_id="c-red"), "triage_dental_emergency",
                      {"severity_level": "urgent", "swelling_or_bleeding_red_flag": True, "symptom_summary": "swelling to eye"})
    lead = unseal(await Repos(db, t.id).leads.find_one({"_id": red["lead_id"]}))
    assert lead["problem"] == "swelling to eye" and lead["symptom_consent"] is True


# ── Cosmetic and implant leads ───────────────────────────

SERVICES = [
    {"name": "Implant consultation", "category": "implant", "estimated_value_minor": 450000},
    {"name": "All-on-4 consultation", "category": "implant", "estimated_value_minor": 2500000},
    {"name": "Invisalign consultation", "category": "cosmetic", "estimated_value_minor": 650000},
    {"name": "Veneers consultation", "category": "cosmetic", "estimated_value_minor": 800000},
    {"name": "Check-up", "category": "routine"},
    {"name": "Old offer", "category": "cosmetic", "estimated_value_minor": 1, "active": False},
]


def test_estimate_value_prefers_the_named_service_then_treatment_words_then_category():
    assert estimate_value(SERVICES, "veneers", "Invisalign consultation") == 650000
    assert estimate_value(SERVICES, "all_on_4", None) == 2500000
    assert estimate_value(SERVICES, "clear_aligners", "") == 650000
    assert estimate_value(SERVICES, "whitening", None) == 650000       # first cosmetic service
    assert estimate_value(SERVICES, "implants", "no such thing") == 450000
    assert estimate_value([{"name": "Check-up", "category": "routine"}], "veneers", None) is None


async def test_qualify_cosmetic_lead_saves_value_currency_and_priority(db, sent):
    t = _tenant(**_owner())
    repos = Repos(db, t.id)
    for i, svc in enumerate(SERVICES):
        await repos.services.insert_one({"_id": f"svc_{i}", "active": svc.pop("active", True), **svc})
    r = await _call(_ctx(t), "qualify_cosmetic_lead",
                    {"treatment_category": "all_on_4", "patient_status": "new", "timeframe": "asap",
                     "health_fund_or_finance_interest": "finance", "caller_name": "Jo", "notes": "missing upper teeth"})
    assert r["success"] and "consultation" in r["message"]
    stored = await repos.leads.find_one({"_id": r["lead_id"]})
    assert "missing upper teeth" not in str(stored)                  # symptoms and notes are encrypted
    lead = unseal(stored)
    assert lead["notes"] == "missing upper teeth"
    assert lead["kind"] == "cosmetic_consult" and lead["estimated_value_minor"] == 2500000
    assert lead["currency"] == "AUD" and lead["priority"] == "high" and lead["urgency"] == "today"
    assert lead["problem"].startswith("All-on-4") and lead["phone"] == CALLER
    await drain()
    assert any("enquiry" in str(a[1]) for a in sent)
    bad = await _call(_ctx(t, phone=None, call_id="call-x"), "qualify_cosmetic_lead",
                      {"treatment_category": "veneers", "patient_status": "new", "timeframe": "researching",
                       "health_fund_or_finance_interest": "none"})
    assert bad["error"] == "phone_required"


# ── Isolation and erasure ────────────────────────────────

async def test_dental_leads_are_tenant_scoped_and_erased_with_the_caller(db):
    a, b = _tenant("ten_da"), _tenant("ten_db")
    r = await _call(_ctx(a), "triage_dental_emergency",
                    {"symptom_summary": "toothache", "severity_level": "urgent", "swelling_or_bleeding_red_flag": False})
    await _call(_ctx(a), "qualify_cosmetic_lead",
                {"treatment_category": "veneers", "patient_status": "existing", "timeframe": "1_3_months",
                 "health_fund_or_finance_interest": "none"})
    assert await Repos(db, b.id).leads.count({}) == 0
    assert await Repos(db, b.id).leads.find_one({"_id": r["lead_id"]}) is None
    assert await Repos(db, a.id).leads.count({}) == 2
    await subjects.erase(db, a, CALLER, actor="usr_test")
    assert await Repos(db, a.id).leads.count({}) == 0


async def test_erasure_reaches_questions_legacy_sessions_and_escalations(db):
    # Audit PRIV-06: every place a caller's number or words can end up.
    t = _tenant("ten_erase")
    repos = Repos(db, t.id)
    await repos.unanswered.insert_one({"_id": "uq_1", "question": "Do you do braces?", "caller_phone": CALLER})
    await db.call_sessions.insert_one({"_id": "cs_1", "tenant_id": t.id, "call_sid": "CA1", "phone": CALLER})
    await repos.leads.insert_one({"_id": "lead_1", "phone": CALLER, "name": "Sam"})
    await repos.escalations.insert_one({"_id": "esc_1", "lead_id": "lead_1", "details_enc": "x"})
    await repos.escalations.insert_one({"_id": "esc_other", "lead_id": "lead_someone_else"})
    out = await subjects.erase(db, t, CALLER, actor="usr_test")
    assert out["counts"]["unanswered"] == 1 and out["counts"]["call_sessions"] == 1
    assert out["counts"]["escalations"] == 1
    assert [e["_id"] for e in await repos.escalations.find({})] == ["esc_other"]
    assert await db.call_sessions.count_documents({"tenant_id": t.id}) == 0
