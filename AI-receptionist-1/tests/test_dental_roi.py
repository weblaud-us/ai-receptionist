"""Dental Phase 5: ROI KPIs in value_report and the monthly e-mail (no double counting of pipeline value)."""

from datetime import datetime, timedelta, timezone

from core.repositories import Repos
from core.services import insights
from core.services.tenant_admin import create_tenant

NOW = datetime.now(timezone.utc)
DAY = NOW - timedelta(days=1)


async def _practice(db, tenant_id="ten_roi", vertical="dental"):
    t = await create_tenant(name="Harbour Smiles", vertical=vertical, country="AU", currency="AUD",
                            tenant_id=tenant_id, seed_template=False, status="active")
    repos = Repos(db, t.id)
    calls = [
        {"answer_window": "lunch_break", "overflow": False, "outcome": "booked"},
        {"answer_window": "after_hours", "overflow": False, "outcome": "message"},
        {"answer_window": "after_hours", "overflow": False, "outcome": "completed", "recovery_sent_at": DAY},
        {"answer_window": "open", "overflow": True, "outcome": "booked"},
        {"answer_window": "open", "overflow": False, "outcome": "faq"},            # reception could have taken it
        {"after_hours": True, "outcome": "message"},                              # an older call: no answer_window
        {"answer_window": "after_hours", "outcome": "busy"},                      # refused at the limit: not answered
    ]
    for i, c in enumerate(calls):
        await repos.calls.insert_one({"call_id": f"{tenant_id}_c{i}", "channel": "phone", "direction": "inbound",
                                      "started_at": DAY, "duration_s": 60, **c})
    for sid, cat, value in (("svc_imp", "implant", 2_500_000), ("svc_inv", "cosmetic", 650_000),
                            ("svc_exam", "routine", 20_000)):
        await repos.services.insert_one({"_id": f"{tenant_id}_{sid}", "name": sid, "category": cat,
                                         "estimated_value_minor": value, "active": True})
    for aid, svc, status, call in (("a1", "svc_imp", "confirmed", "c0"), ("a2", "svc_inv", "cancelled", "c3"),
                                   ("a3", "svc_exam", "confirmed", "c3"), ("a4", "svc_inv", "confirmed", "c3")):
        await repos.appointments.insert_one({"appointment_id": f"{tenant_id}_{aid}", "service_id": f"{tenant_id}_{svc}",
                                             "status": status, "source": "voice", "call_id": f"{tenant_id}_{call}"})
    leads = [
        {"kind": "cosmetic_consult", "estimated_value_minor": 2_500_000, "call_id": f"{tenant_id}_c0"},  # booked: a1
        {"kind": "cosmetic_consult", "estimated_value_minor": 800_000, "call_id": f"{tenant_id}_c1"},    # not booked
        {"kind": "cosmetic_consult", "estimated_value_minor": None, "call_id": f"{tenant_id}_c2"},
        {"kind": "dental_emergency", "call_id": f"{tenant_id}_c5"},
    ]
    for i, lead in enumerate(leads):
        await repos.leads.insert_one({"_id": f"{tenant_id}_l{i}", "phone": "+61400000000", **lead})
    return t


async def test_dental_roi_counts_overflow_consults_triage_recoveries_and_pipeline(db):
    t = await _practice(db)
    r = await insights.value_report(db, t, NOW - timedelta(days=7), NOW + timedelta(minutes=10))
    d = r["dental"]
    assert d["by_window"] == {"lunch_break": 1, "after_hours": 3, "busy_overflow": 1}
    assert d["overflow_calls_answered"] == 5
    assert d["consultations_booked"] == 2                  # implant + Invisalign; the cancelled and routine don't count
    assert d["cosmetic_leads"] == 3 and d["emergency_triages_captured"] == 1
    assert d["missed_call_sms_recoveries"] == 1
    # 25,000 (implant consult) + 6,500 (Invisalign consult) + 8,000 (unbooked lead). The lead on call c0
    # was booked as a1, so it isn't counted again.
    assert d["estimated_pipeline_value_minor"] == 2_500_000 + 650_000 + 800_000
    assert d["currency"] == "AUD"


async def test_dental_lines_in_the_monthly_email_and_none_for_other_businesses(db):
    t = await _practice(db)
    r = await insights.value_report(db, t, NOW - timedelta(days=7), NOW + timedelta(minutes=10))
    text = insights.report_text(t, r, "Your assistant in November:")
    assert "For your practice:" in text and "1 at lunch, 3 after hours, 1 while busy" in text
    assert "Estimated treatment pipeline: A$39,500" in text
    salon = await _practice(db, tenant_id="ten_salon_roi", vertical="salon")
    plain = await insights.value_report(db, salon, NOW - timedelta(days=7), NOW + timedelta(minutes=10))
    assert "dental" not in plain
    assert "For your practice" not in insights.report_text(salon, plain, "x")


def test_money_formatting_in_minor_units():
    assert insights.money_minor(2_500_000, "AUD") == "A$25,000"
    assert insights.money_minor(200_000, "GBP") == "£2,000"
    assert insights.money_minor(12_300, "SEK") == "123 SEK"
