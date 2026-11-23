"""Plan 4.5: "add to calendar" links on bookings, and the Monday summary e-mail."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from config import settings
from core.repositories import Repos
from core.services import appointment_ics
from core.services.tenant_admin import create_tenant
from core.tenancy import invalidate, resolve_by_id

SYD = "Australia/Sydney"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_BASE_URL", "https://api.example.com")
    invalidate()
    yield
    invalidate()


async def _practice(db, **extra):
    t = await create_tenant(name="Harbour Smiles, Bondi", vertical="dental", country="AU", timezone=SYD,
                            tenant_id="ten_cal", status="active", seed_template=False, phone="+61290000000",
                            extra={"address": {"line1": "1 Beach Rd", "city": "Bondi"}, **extra})
    return await resolve_by_id(t.id)


def test_the_calendar_file_is_valid_and_escaped():
    from core.tenancy.models import TenantConfig

    t = TenantConfig.model_validate({"_id": "ten_x", "name": "Harbour Smiles, Bondi", "slug": "hs", "phone": "+6129"})
    start = datetime(2026, 12, 1, 23, 30, tzinfo=timezone.utc)
    text = appointment_ics.ics_text(t, {"appointment_id": "apt_1", "service_name": "Check-up; clean",
                                        "resource_name": "Dr Lee", "start": start, "end": start + timedelta(minutes=45)})
    assert "DTSTART:20261201T233000Z" in text and "DTEND:20261202T001500Z" in text
    assert "SUMMARY:Check-up\\; clean at Harbour Smiles\\, Bondi with Dr Lee" in text.replace("\r\n ", "")
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")


async def test_the_link_is_signed_and_a_cancelled_booking_says_so(db, client):
    t = await _practice(db)
    start = datetime.now(timezone.utc) + timedelta(days=2)
    await Repos(db, t.id).appointments.insert_one({"_id": "apt_1", "appointment_id": "apt_1", "status": "confirmed",
                                                   "service_name": "Check-up", "start": start,
                                                   "end": start + timedelta(minutes=30)})
    url = appointment_ics.ics_url(t.id, "apt_1").replace("https://api.example.com", "")
    r = client.get(url)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar") and "Check-up" in r.text
    assert client.get(url.split("?")[0] + "?sig=forged").status_code == 404
    await Repos(db, t.id).appointments.update_one({"appointment_id": "apt_1"}, {"$set": {"status": "cancelled"}})
    assert client.get(url).status_code == 410


async def test_the_monday_summary_goes_once_a_week_and_not_to_pilots(db, monkeypatch):
    from core.integrations import email as email_api
    from services import tenant_jobs

    sent: list = []

    async def fake_email(to, subject, text):
        sent.append((to, subject, text))
        return True

    monkeypatch.setattr(tenant_jobs, "send_email", fake_email)
    monkeypatch.setattr(email_api, "send_email", fake_email)
    t = await _practice(db, notifications={"owner_email": "owner@hs.au"})
    await db.tenants.update_one({"_id": t.id}, {"$set": {"created_at": datetime.now(timezone.utc) - timedelta(days=30)}})
    invalidate()
    t = await resolve_by_id(t.id)
    monday = datetime(2026, 11, 30, 9, 30, tzinfo=ZoneInfo(SYD)).astimezone(timezone.utc)
    monkeypatch.setattr(settings, "REPORT_HOUR", 8)
    assert await tenant_jobs.maybe_send_weekly_report(t, monday) is True
    assert sent and sent[0][1] == "Harbour Smiles, Bondi: your assistant last week"
    assert await tenant_jobs.maybe_send_weekly_report(t, monday + timedelta(hours=2)) is False     # once a week
    assert await tenant_jobs.maybe_send_weekly_report(t, monday + timedelta(days=1)) is False      # Tuesday


async def test_the_booking_confirmation_carries_the_calendar_link(db, monkeypatch):
    import json

    import core.integrations.whatsapp as wa
    from core.notifications import drain
    from core.tools.registry import ToolContext, run_tool

    texts: list = []

    async def fake_text(to, text, pnid=None):
        texts.append(text)
        return True

    monkeypatch.setattr(wa, "send_text", fake_text)
    monkeypatch.setattr(wa, "configured", lambda pnid=None: True)
    t = await _practice(db, hours=[{"dow": d, "open": "08:00", "close": "18:00"} for d in range(7)],
                        scheduling={"min_notice_minutes": 0})
    repos = Repos(db, t.id)
    await repos.resources.insert_one({"_id": "res_lee", "name": "Dr Lee", "kind": "staff", "active": True})
    await repos.services.insert_one({"_id": "svc", "name": "Check-up", "duration_min": 30, "active": True, "sort": 1})
    now = datetime.now(timezone.utc)
    ctx = ToolContext(channel="voice", caller_phone="+61412345678", call_id="c1", tenant=t, now=now)
    day = (now.astimezone(ZoneInfo(SYD)) + timedelta(days=2)).date().isoformat()
    slots = json.loads(await run_tool(ctx, "check_availability", {"service": "Check-up", "date": day}))
    booked = json.loads(await run_tool(ctx, "book_appointment", {"slot_id": slots["slots"][0]["slot_id"],
                                                                  "service": "Check-up", "name": "Mia Chen",
                                                                  "date_of_birth": "2014-03-09"}))
    await drain()
    assert booked["success"] and texts
    assert f"https://api.example.com/public/appointments/{t.id}/{booked['appointment_id']}.ics?sig=" in texts[-1]
