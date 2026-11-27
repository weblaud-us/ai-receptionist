"""scripts/seed_sales_tenant.py: your own business as the sales dialer's tenant; re-runs update it."""

from datetime import date, datetime, timezone

import pytest

from core.repositories import Repos
from core.services.scheduling import SchedulingService
from core.tenancy import invalidate, resolve_by_id
from scripts.seed_sales_tenant import TENANT_ID, parse_days, seed


def test_parse_days():
    assert parse_days("mon-fri") == [0, 1, 2, 3, 4]
    assert parse_days("mon, wed,sat") == [0, 2, 5]
    for bad in ("fri-mon", "funday", ""):
        with pytest.raises(ValueError):
            parse_days(bad)


async def test_seed_creates_a_bookable_sales_tenant_and_reruns_update_it(db):
    assert await seed(db, name="WebLaud", timezone="Asia/Dhaka", country="US", currency="USD",
                      days=[0, 1, 2, 3, 4], open_="09:00", close="17:00") == TENANT_ID
    t = await resolve_by_id(TENANT_ID)
    assert (t.name, t.vertical, t.status, t.timezone, t.country) == ("WebLaud", "professional", "active", "Asia/Dhaka", "US")
    assert len(t.hours) == 5 and not t.transfer.number
    assert not t.notifications.on_new_booking and not t.notifications.daily_digest      # no SMS, no alerts unasked
    services = await Repos(db, TENANT_ID).services.find({})
    assert [(s["name"], s["duration_min"]) for s in services] == [("Demo", 15)]           # no template services

    svc = SchedulingService(Repos(db, TENANT_ID), t)
    demo = svc.match_service(await svc.services(), "Demo")
    monday = date(2027, 1, 4)
    slots = await svc.find_slots(service=demo, day_from=monday, day_to=monday, requested_at=None,
                                 now=datetime(2027, 1, 3, tzinfo=timezone.utc))
    assert slots                                                    # a business with no staff still has times

    await seed(db, name="WebLaud", timezone="America/New_York", country="US", currency="USD", days=[0, 2],
               open_="10:00", close="16:00", owner_email="founder@example.com", transfer="+15125550100")
    invalidate()
    t = await resolve_by_id(TENANT_ID)
    assert t.timezone == "America/New_York" and [h.dow for h in t.hours] == [0, 2]
    assert t.transfer.number == "+15125550100" and t.notifications.on_new_booking == ["email"]
    assert len(await Repos(db, TENANT_ID).services.find({})) == 1                         # not duplicated
