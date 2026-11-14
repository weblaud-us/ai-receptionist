"""SchedulingService: generic booking for any vertical, race-free via slot_locks."""

import asyncio
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from core.repositories import Repos
from core.services.scheduling import SchedulingError, SchedulingService
from core.tenancy.models import HoursEntry, TenantConfig

UTC = timezone.utc
TZ = "America/Chicago"


def _tenant(tenant_id="ten_salon", **kw) -> TenantConfig:
    base = dict(_id=tenant_id, name="Bella Salon", slug=tenant_id.replace("_", "-"), vertical="salon",
                timezone=TZ, hours=[HoursEntry(dow=d, open="09:00", close="17:00") for d in range(7)])
    base.update(kw)
    t = TenantConfig(**base)
    t.scheduling.min_notice_minutes = 0
    return t


async def _seed(db, tenant_id="ten_salon"):
    repos = Repos(db, tenant_id)
    anna = await repos.resources.insert_one({"_id": f"res_anna_{tenant_id}", "name": "Anna", "kind": "staff",
                                             "hours": None, "capacity": 1, "active": True})
    mo = await repos.resources.insert_one({"_id": f"res_mo_{tenant_id}", "name": "Mo", "kind": "staff",
                                           "hours": [{"dow": d, "open": "12:00", "close": "17:00"} for d in range(7)],
                                           "capacity": 1, "active": True})
    cut = await repos.services.insert_one({"_id": f"svc_cut_{tenant_id}", "name": "Haircut & blow-dry",
                                           "duration_min": 45, "buffer_min": 15, "resource_ids": [anna["_id"], mo["_id"]],
                                           "price": {"amount": 4500, "currency": "USD", "display": "from $45"},
                                           "ai_bookable": True, "active": True, "sort": 1})
    return repos, anna, mo, cut


# Monday 23 Nov 2026, 08:00 local
NOW = datetime(2026, 11, 23, 8, 0, tzinfo=ZoneInfo(TZ)).astimezone(UTC)
DAY = date(2026, 11, 23)


def _at(hh, mm=0, day=DAY):
    return datetime.combine(day, datetime.min.time().replace(hour=hh, minute=mm), tzinfo=ZoneInfo(TZ)).astimezone(UTC)


async def test_find_slots_respects_staff_hours_and_ranks_by_request(db):
    repos, anna, mo, cut = await _seed(db)
    svc = SchedulingService(repos, _tenant())
    slots = await svc.find_slots(service=cut, day_from=DAY, requested_at=_at(15), now=NOW, limit=3)
    assert [s.start for s in slots] == [_at(15), _at(14, 45), _at(15, 15)]
    mo_only = await svc.find_slots(service=cut, day_from=DAY, resource=mo, requested_at=_at(9), now=NOW)
    assert mo_only[0].start == _at(12)          # Mo starts at noon


async def test_match_service_is_forgiving():
    services = [{"_id": "a", "name": "Haircut & blow-dry"}, {"_id": "b", "name": "Beard trim"}]
    assert SchedulingService.match_service(services, "a haircut")["_id"] == "a"
    assert SchedulingService.match_service(services, "beard")["_id"] == "b"
    assert SchedulingService.match_service(services, "haircut and blow dry")["_id"] == "a"
    assert SchedulingService.match_service(services, "massage") is None
    assert SchedulingService.match_service(services[:1], None)["_id"] == "a"


async def test_book_then_slot_is_gone_and_cancel_frees_it(db):
    repos, anna, mo, cut = await _seed(db)
    svc = SchedulingService(repos, _tenant())
    appt = await svc.book(resource_id=anna["_id"], start=_at(10), service=cut, name="Rahim", phone="+15125550100", now=NOW)
    assert appt["status"] == "confirmed" and appt["end"] == _at(10, 45)
    assert await repos.slot_locks.count({"appointment_id": appt["appointment_id"]}) == 9    # 45 min in 5-min blocks

    slots = await svc.find_slots(service=cut, day_from=DAY, resource=anna, requested_at=_at(10), now=NOW, limit=10)
    assert _at(10) not in [s.start for s in slots]
    assert _at(10, 45) not in [s.start for s in slots]   # buffer after
    assert _at(11) in [s.start for s in slots]
    assert _at(9, 15) in [s.start for s in slots]        # ends exactly at 10:00 — allowed
    assert _at(9, 30) not in [s.start for s in slots]    # 9:30 + 45 min overlaps the 10:00 booking

    with pytest.raises(SchedulingError) as e:
        await svc.book(resource_id=anna["_id"], start=_at(10), service=cut, name="Other", phone="+15125550101", now=NOW)
    assert e.value.code == "taken"

    await svc.cancel(appt["appointment_id"], "changed plans")
    assert await repos.slot_locks.count({}) == 0
    again = await svc.book(resource_id=anna["_id"], start=_at(10), service=cut, name="Sara", phone="+15125550102", now=NOW)
    assert again["status"] == "confirmed"


async def test_concurrent_bookings_of_the_same_slot_exactly_one_wins(db):
    repos, anna, mo, cut = await _seed(db)
    svc = SchedulingService(repos, _tenant())
    barrier = asyncio.Barrier(5)
    original = svc.find_slots

    async def check_then_wait(*a, **k):          # everyone sees the slot free, then all claim it
        r = await original(*a, **k)
        await barrier.wait()
        return r

    svc.find_slots = check_then_wait  # type: ignore[method-assign]

    async def attempt(i):
        try:
            return await svc.book(resource_id=anna["_id"], start=_at(13), service=cut, name=f"P{i}",
                                  phone=f"+1512555020{i}", now=NOW)
        except SchedulingError as e:
            return e.code

    results = await asyncio.gather(*[attempt(i) for i in range(5)])
    winners = [r for r in results if isinstance(r, dict)]
    assert len(winners) == 1 and results.count("taken") == 4
    assert await repos.appointments.count({"status": "confirmed"}) == 1


async def test_reschedule_locks_new_before_releasing_old(db):
    repos, anna, mo, cut = await _seed(db)
    svc = SchedulingService(repos, _tenant())
    appt = await svc.book(resource_id=anna["_id"], start=_at(10), service=cut, name="Rahim", phone="+15125550100", now=NOW)
    moved = await svc.reschedule(appt["appointment_id"], resource_id=mo["_id"], start=_at(14), now=NOW)
    assert moved["resource_id"] == mo["_id"] and moved["rescheduled_from"] == appt["appointment_id"]
    old = await repos.appointments.find_one({"appointment_id": appt["appointment_id"]})
    assert old["status"] == "cancelled" and old["rescheduled_to"] == moved["appointment_id"]
    assert await repos.slot_locks.count({"appointment_id": appt["appointment_id"]}) == 0
    assert await repos.slot_locks.count({"appointment_id": moved["appointment_id"]}) == 9
    # Rescheduling onto a taken slot leaves the original untouched.
    other = await svc.book(resource_id=anna["_id"], start=_at(11), service=cut, name="X", phone="+15125550199", now=NOW)
    with pytest.raises(SchedulingError):
        await svc.reschedule(moved["appointment_id"], resource_id=anna["_id"], start=_at(11), now=NOW)
    still = await repos.appointments.find_one({"appointment_id": moved["appointment_id"]})
    assert still["status"] == "confirmed" and other["status"] == "confirmed"


async def test_booking_outside_hours_or_too_soon_is_refused(db):
    repos, anna, mo, cut = await _seed(db)
    svc = SchedulingService(repos, _tenant())
    with pytest.raises(SchedulingError) as e:
        await svc.book(resource_id=anna["_id"], start=_at(18), service=cut, name="A", phone="+1555", now=NOW)
    assert e.value.code == "taken"
    t = _tenant()
    t.scheduling.min_notice_minutes = 120
    strict = SchedulingService(repos, t)
    with pytest.raises(SchedulingError) as e:
        await strict.book(resource_id=anna["_id"], start=_at(9), service=cut, name="A", phone="+1555", now=NOW)
    assert e.value.code == "too_soon"


async def test_tenants_never_see_each_others_appointments(db):
    repos_a, anna_a, _, cut_a = await _seed(db, "ten_a")
    repos_b, anna_b, _, cut_b = await _seed(db, "ten_b")
    a = SchedulingService(repos_a, _tenant("ten_a"))
    b = SchedulingService(repos_b, _tenant("ten_b"))
    await a.book(resource_id=anna_a["_id"], start=_at(10), service=cut_a, name="A", phone="+15125550100", now=NOW)
    # Same phone, same time, other tenant: free.
    appt_b = await b.book(resource_id=anna_b["_id"], start=_at(10), service=cut_b, name="B", phone="+15125550100", now=NOW)
    assert appt_b["status"] == "confirmed"
    assert [x["name"] for x in await a.upcoming_for_phone(["+15125550100"], now=NOW)] == ["A"]
    assert [x["name"] for x in await b.upcoming_for_phone(["+15125550100"], now=NOW)] == ["B"]
    with pytest.raises(SchedulingError):
        await a.cancel(appt_b["appointment_id"])        # B's appointment doesn't exist for A


async def test_business_with_no_resources_still_books(db):
    repos = Repos(db, "ten_solo")
    svc_doc = await repos.services.insert_one({"_id": "svc_x", "name": "Consultation", "duration_min": 30, "active": True})
    svc = SchedulingService(repos, _tenant("ten_solo"))
    slots = await svc.find_slots(service=svc_doc, day_from=DAY, now=NOW)
    assert slots and slots[0].resource_id == "any"
    appt = await svc.book(resource_id="any", start=slots[0].start, service=svc_doc, name="N", phone="+1555", now=NOW)
    assert appt["status"] == "confirmed"
    with pytest.raises(SchedulingError):
        await svc.book(resource_id="any", start=slots[0].start, service=svc_doc, name="M", phone="+1556", now=NOW)
