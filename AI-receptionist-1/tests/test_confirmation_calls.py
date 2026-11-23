"""Phase 2.5: day-before confirmation calls for patients the WhatsApp reminder didn't reach."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from config import settings
from core.repositories import Repos
from core.services import confirmation_calls as cc
from core.tenancy.models import HoursEntry, TenantConfig

SYD = "Australia/Sydney"
NOW = datetime(2026, 11, 23, 14, 0, tzinfo=ZoneInfo(SYD)).astimezone(timezone.utc)   # Monday 2 p.m.


def _tenant(**cfg) -> TenantConfig:
    return TenantConfig(_id="ten_cc", name="Harbour Smiles", slug="hs", vertical="dental", country="AU",
                        timezone=SYD, phone="+61290000000",
                        hours=[HoursEntry(dow=d, open="09:00", close="17:00") for d in range(5)],
                        confirmation_calls={"enabled": True, **cfg})


@pytest.fixture
def dialled(monkeypatch):
    rooms: list = []

    async def fake_dispatch(room, metadata):
        rooms.append((room, metadata))

    monkeypatch.setattr(cc, "dispatch", fake_dispatch)
    monkeypatch.setattr(settings, "LIVEKIT_SIP_OUTBOUND_TRUNK_ID", "ST_out")
    monkeypatch.setattr(settings, "LIVEKIT_URL", "wss://lk.example")
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", "key")
    return rooms


async def _seed(db, t, appt_id, *, hours_ahead=20, reminder_error="send_failed", **kw):
    repos = Repos(db, t.id)
    if not await repos.phone_numbers.find_one({}):
        await repos.phone_numbers.insert_one({"_id": "pn_1", "e164": "+61290000001", "status": "active"})
    start = NOW + timedelta(hours=hours_ahead)
    reminders = {"day_before": {"sent_at": NOW - timedelta(hours=4), **({"error": reminder_error} if reminder_error else {})}}
    await repos.appointments.insert_one({"_id": appt_id, "appointment_id": appt_id, "status": "confirmed",
                                         "name": "Mia Chen", "phone": "+61412345678", "start": start,
                                         "end": start + timedelta(minutes=30), "reminders": reminders, **kw})


async def test_only_patients_the_whatsapp_reminder_missed_are_called(db, dialled):
    t = _tenant()
    await _seed(db, t, "apt_missed")
    await _seed(db, t, "apt_reached", reminder_error=None)              # WhatsApp got through
    await _seed(db, t, "apt_soon", hours_ahead=1)                       # too close to the visit
    await _seed(db, t, "apt_done", confirmation={"status": "confirmed"})
    assert await cc.run_due(t, NOW) == 1
    assert [r[0] for r in dialled] == ["confirm-apt_missed-1"]
    meta = dialled[0][1]
    assert meta == {"kind": cc.KIND, "tenant_id": "ten_cc", "appointment_id": "apt_missed", "from": "+61290000001"}
    row = await Repos(db, t.id).appointments.find_one({"appointment_id": "apt_missed"})
    assert row["confirmation"]["status"] == "calling" and row["confirmation"]["attempts"] == 1


async def test_a_practice_can_confirm_every_appointment(db, dialled):
    t = _tenant(only_without_whatsapp=False)
    await _seed(db, t, "apt_reached", reminder_error=None)
    assert await cc.run_due(t, NOW) == 1


async def test_no_double_calls_one_retry_then_stop(db, dialled):
    t = _tenant()
    await _seed(db, t, "apt_1")
    assert await cc.run_due(t, NOW) == 1
    assert await cc.run_due(t, NOW + timedelta(minutes=5)) == 0                 # already calling
    await cc.record_outcome(t.id, "apt_1", "no_answer", call_id="confirm-apt_1-1")
    assert await cc.run_due(t, NOW + timedelta(minutes=30)) == 0                # too soon to retry
    assert await cc.run_due(t, NOW + timedelta(hours=2, minutes=1)) == 1        # one retry
    await cc.record_outcome(t.id, "apt_1", "voicemail", call_id="confirm-apt_1-2")
    assert await cc.run_due(t, NOW + timedelta(hours=5)) == 0                   # two attempts: done
    assert [r[0] for r in dialled] == ["confirm-apt_1-1", "confirm-apt_1-2"]


async def test_never_outside_hours_on_sundays_or_to_do_not_contact(db, dialled):
    t = _tenant(window="10:00-18:00")
    await _seed(db, t, "apt_1")
    evening = datetime(2026, 11, 23, 19, 0, tzinfo=ZoneInfo(SYD))
    sunday = datetime(2026, 11, 29, 11, 0, tzinfo=ZoneInfo(SYD))
    assert not cc.in_window(t, evening) and not cc.in_window(t, sunday) and cc.in_window(t, NOW)
    repos = Repos(db, t.id)
    await repos.contacts.insert_one({"_id": "con_x", "phone": "+61400000009", "do_not_contact": True})
    await _seed(db, t, "apt_dnc", contact_id="con_x")
    assert await cc.run_due(t, NOW) == 1                                       # apt_1 only
    row = await repos.appointments.find_one({"appointment_id": "apt_dnc"})
    assert row["confirmation"] == {"status": "skipped", "reason": "do_not_contact"}


async def test_nothing_is_dialled_without_a_trunk_or_a_practice_number(db, dialled, monkeypatch):
    t = _tenant()
    monkeypatch.setattr(settings, "LIVEKIT_SIP_OUTBOUND_TRUNK_ID", "")
    await _seed(db, t, "apt_1")
    assert await cc.run_due(t, NOW) == 0
    monkeypatch.setattr(settings, "LIVEKIT_SIP_OUTBOUND_TRUNK_ID", "ST_out")
    await Repos(db, t.id).phone_numbers.delete_many({})
    assert await cc.run_due(t, NOW) == 0 and not dialled


async def test_a_moved_booking_counts_as_confirmed(db):
    t = _tenant()
    await _seed(db, t, "apt_old", rescheduled_to="apt_new")
    await _seed(db, t, "apt_new", hours_ahead=44)
    await cc.record_outcome(t.id, "apt_old", "rescheduled", call_id="confirm-apt_old-1")
    new = await Repos(db, t.id).appointments.find_one({"appointment_id": "apt_new"})
    assert new["confirmation"]["status"] == "confirmed"


def test_the_opener_discloses_ai_and_names_no_treatment():
    text = cc.opener(agent_name="Aria", business="Harbour Smiles", patient_first_name="Mia",
                     when="tomorrow at 10 30 a.m.")
    assert text.startswith("Hi, this is Aria, the AI assistant at Harbour Smiles.")
    assert "Mia's appointment tomorrow at 10 30 a.m." in text
    vm = cc.voicemail(business="Harbour Smiles", phone="02 9000 0000")
    assert "tomorrow" not in vm and "appointment" in vm and "02 9000 0000" in vm


def test_outcomes():
    assert cc.outcome_of(True, None, "human", True) == "confirmed"
    assert cc.outcome_of(False, "rescheduled", "human", True) == "rescheduled"
    assert cc.outcome_of(False, None, "machine-vm", True) == "voicemail"
    assert cc.outcome_of(False, None, None, False) == "no_answer"
    assert cc.outcome_of(False, "completed", "human", True) == "no_decision"
    assert "no_decision" in cc.FINAL and "no_answer" not in cc.FINAL


def test_the_calling_window_is_validated():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _tenant(window="10am-6pm")
