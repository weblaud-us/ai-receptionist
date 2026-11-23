"""Phase 2.3: an urgent dental call pages the on-call list until someone acknowledges it."""

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from config import settings
from core.notifications import drain
from core.repositories import Repos
from core.services import escalation
from core.services.tenant_admin import create_tenant
from core.tenancy import invalidate, resolve_by_id
from core.tools.hotel import drain_background_tasks
from core.tools.registry import ToolContext, run_tool

SYD = "Australia/Sydney"
NOW = datetime(2026, 11, 23, 21, 0, tzinfo=ZoneInfo(SYD)).astimezone(timezone.utc)   # after hours
ON_CALL = [{"name": "Dr Lee", "whatsapp": "+61400000011", "email": "lee@hs.au"},
           {"name": "Dr Patel", "whatsapp": "", "email": "patel@hs.au"}]


@pytest.fixture(autouse=True)
async def sent(monkeypatch):
    import core.integrations.email as email_api
    import core.integrations.whatsapp as wa

    out: list = []

    async def fake_wa(to, text, pnid=None):
        out.append(("whatsapp", to, text))
        return True

    async def fake_email(to, subject, text):
        out.append(("email", to, text))
        return True

    monkeypatch.setattr(wa, "send_text", fake_wa)
    monkeypatch.setattr(email_api, "send_email", fake_email)
    monkeypatch.setattr(settings, "WEBHOOK_BASE_URL", "https://api.example.com")
    invalidate()
    yield out
    await drain_background_tasks()
    await drain()
    invalidate()


async def _tenant(db, *, enabled=True, tenant_id="ten_esc"):
    t = await create_tenant(name="Harbour Smiles", vertical="dental", timezone=SYD, country="AU", currency="AUD",
                            tenant_id=tenant_id, status="active", seed_template=False,
                            extra={"escalation": {"enabled": enabled, "on_call": ON_CALL, "ack_minutes": 10},
                                   "notifications": {"owner_email": "owner@hs.au"}})
    return await resolve_by_id(t.id)


async def _triage(t, severity="urgent"):
    ctx = ToolContext(channel="voice", caller_phone="+61412345678", call_id="call-esc", tenant=t, now=NOW)
    out = json.loads(await run_tool(ctx, "triage_dental_emergency", {
        "symptom_summary": "Broken tooth, throbbing pain", "severity_level": severity, "patient_name": "Sam Smith",
        "symptom_consent": True}))
    await drain()
    return out


async def test_an_urgent_call_alerts_the_first_person_on_call(db, sent):
    t = await _tenant(db)
    out = await _triage(t)
    assert out["success"]
    rows = await Repos(db, t.id).escalations.find({})
    assert len(rows) == 1 and rows[0]["status"] == "open" and rows[0]["step"] == 0
    assert "Broken tooth" not in json.dumps(rows[0], default=str)          # symptoms are encrypted at rest
    to_lee = [m for m in sent if m[1] in ("+61400000011", "lee@hs.au")]
    assert {m[0] for m in to_lee} == {"whatsapp", "email"}
    assert "Broken tooth" in to_lee[0][2] and "https://api.example.com/public/escalations/" in to_lee[0][2]
    assert not any(m[1] == "patel@hs.au" for m in sent)
    lead = await Repos(db, t.id).leads.find_one({"_id": out["lead_id"]})
    assert lead["escalation_status"] == "open"


async def test_nobody_acknowledging_moves_down_the_list_then_to_the_owner(db, sent):
    t = await _tenant(db)
    await _triage(t)
    assert await escalation.run_due(t, NOW + timedelta(minutes=5)) == 0             # still Dr Lee's turn
    assert await escalation.run_due(t, NOW + timedelta(minutes=11)) == 1
    assert any(m[1] == "patel@hs.au" for m in sent)
    assert await escalation.run_due(t, NOW + timedelta(minutes=22)) == 1
    assert any(m[1] == "owner@hs.au" and "Nobody on call" in m[2] for m in sent)
    row = await Repos(db, t.id).escalations.find_one({})
    assert row["status"] == "unacknowledged" and [h["to"] for h in row["history"]] == ["Dr Lee", "Dr Patel", "owner"]
    assert await escalation.run_due(t, NOW + timedelta(hours=2)) == 0             # it stops there


async def test_the_alert_link_acknowledges_only_on_the_button(db, client, sent):
    t = await _tenant(db)
    await _triage(t)
    row = await Repos(db, t.id).escalations.find_one({})
    url = escalation.ack_url(t.id, row["_id"]).replace("https://api.example.com", "")
    page = client.get(url)
    assert page.status_code == 200 and "handling it" in page.text
    assert (await Repos(db, t.id).escalations.find_one({}))["status"] == "open"     # a link scanner changes nothing
    assert client.post(url.split("?")[0] + "?sig=forged").status_code == 404
    assert client.post(url).status_code == 200
    assert (await Repos(db, t.id).escalations.find_one({}))["status"] == "acknowledged"
    assert await escalation.run_due(t, NOW + timedelta(hours=1)) == 0
    assert "Already being handled" in client.get(url).text


async def test_routine_problems_or_no_on_call_list_page_nobody(db, sent):
    t = await _tenant(db)
    await _triage(t, severity="routine")
    assert await Repos(db, t.id).escalations.count({}) == 0
    off = await _tenant(db, enabled=False, tenant_id="ten_esc_off")
    await _triage(off)
    assert await Repos(db, off.id).escalations.count({}) == 0


async def test_staff_see_and_acknowledge_escalations_in_the_dashboard(db, client, sent, monkeypatch):
    from core.auth import service as auth_service

    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    auth_service.reset_rate_limits()
    t = await _tenant(db)
    await _triage(t)
    await auth_service.create_user(email="desk@hs.au", password="correct horse battery", tenant_id=t.id, role="staff")
    tok = client.post("/auth/login", json={"email": "desk@hs.au", "password": "correct horse battery"}).json()
    h = {"Authorization": f"Bearer {tok['access_token']}"}
    rows = client.get("/t/escalations", headers=h).json()["escalations"]
    assert len(rows) == 1 and "details_enc" not in rows[0]
    assert client.post(f"/t/escalations/{rows[0]['_id']}/ack", headers=h).status_code == 200
    assert client.post(f"/t/escalations/{rows[0]['_id']}/ack", headers=h).status_code == 409
    leads = client.get("/t/leads", headers=h).json()["leads"]
    lead = next(x for x in leads if x.get("escalation_id") == rows[0]["_id"])
    assert lead["escalation_status"] == "acknowledged" and lead["escalation_acked_by"]
