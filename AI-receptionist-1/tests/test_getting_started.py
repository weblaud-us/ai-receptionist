"""Getting started: each first step ticks itself off from real data (GET /t/getting-started, platform signals)."""

from datetime import datetime, timezone

import pytest

from config import settings
from core.auth import service as auth_service
from core.repositories import Repos
from core.repositories.repos import PhoneNumbersStore
from core.services import getting_started
from core.services.tenant_admin import create_tenant
from core.tenancy import invalidate
from core.tenancy.models import TenantConfig

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    auth_service.reset_rate_limits()
    invalidate()
    yield
    invalidate()


def _login(client, email):
    tok = client.post("/auth/login", json={"email": email, "password": PASSWORD}).json()
    return {"Authorization": f"Bearer {tok['access_token']}"}


async def _practice(db, tenant_id="ten_gs", vertical="dental"):
    t = await create_tenant(name="Harbour Smiles", vertical=vertical, country="AU", timezone="Australia/Sydney",
                            tenant_id=tenant_id, status="active", seed_template=False)
    await auth_service.create_user(email=f"owner@{tenant_id}.au", password=PASSWORD, tenant_id=t.id, role="owner")
    return t


async def _signals(db, tenant_id="ten_gs"):
    return await getting_started.signals(db, TenantConfig.from_doc(await db.tenants.find_one({"_id": tenant_id})))


async def _set(db, tenant_id, fields):
    await db.tenants.update_one({"_id": tenant_id}, {"$set": fields})
    invalidate()


async def test_a_new_practice_has_done_nothing_yet(db):
    await _practice(db)
    assert await _signals(db) == {k: False for k in getting_started.ORDER}


async def test_each_step_ticks_from_its_own_data(db):
    t = await _practice(db)
    repos = Repos(db, t.id)
    now = datetime.now(timezone.utc)

    await _set(db, t.id, {"onboarding": {"step": "number", "completed_at": now.isoformat()}})
    assert (await _signals(db))["setup"]

    await repos.calls.insert_one({"call_id": "test_ab12", "channel": "browser", "outcome": "completed", "started_at": now})
    s = await _signals(db)
    assert s["test_call"] and not s["first_call"]          # a test call is not a real one

    await PhoneNumbersStore(db).assign({"e164": "+61290000000", "tenant_id": t.id})
    assert (await _signals(db))["number"]

    await repos.knowledge.insert_one({"question": "Parking?", "answer": "Street.", "source": "template"})
    assert not (await _signals(db))["knowledge"]           # the wizard's examples don't count
    await repos.knowledge.insert_one({"question": "Payment plans?", "answer": "Yes, over 12 months."})
    assert (await _signals(db))["knowledge"]

    await _set(db, t.id, {"integrations.google_calendar": {"connected": True, "refresh_token_enc": "x"}})
    assert (await _signals(db))["calendar"]

    await db.invites.insert_one({"tenant_id": t.id, "email": "desk@hs.au", "role": "staff"})
    assert (await _signals(db))["team"]

    await _set(db, t.id, {"legal.dpa_accepted_at": now})
    assert (await _signals(db))["dpa"]


async def test_only_an_answered_inbound_phone_call_counts_as_the_first_real_call(db):
    t = await _practice(db)
    repos = Repos(db, t.id)
    now = datetime.now(timezone.utc)
    await repos.calls.insert_one({"call_id": "c1", "channel": "phone", "outcome": "unassigned", "started_at": now})
    await repos.calls.insert_one({"call_id": "c2", "channel": "phone", "direction": "outbound",
                                  "outcome": "completed", "started_at": now})
    await repos.calls.insert_one({"call_id": "c3", "channel": "whatsapp", "outcome": "completed", "started_at": now})
    assert not (await _signals(db))["first_call"]
    await repos.calls.insert_one({"call_id": "c4", "channel": "phone", "direction": "inbound",
                                  "outcome": "completed", "started_at": now})
    assert (await _signals(db))["first_call"]


async def test_a_practice_system_counts_as_a_connected_calendar(db):
    t = await _practice(db)
    await _set(db, t.id, {"integrations.pms": {"provider": "cliniko", "connected": True}})
    assert (await _signals(db))["calendar"]


async def test_a_second_member_counts_as_inviting_the_team(db):
    t = await _practice(db)
    await auth_service.create_user(email="desk@hs.au", password=PASSWORD, tenant_id=t.id, role="staff")
    assert (await _signals(db))["team"]


async def test_the_owner_sees_every_item_and_an_admin_not_the_owners(db, client):
    t = await _practice(db)
    await auth_service.create_user(email="pm@hs.au", password=PASSWORD, tenant_id=t.id, role="admin")
    out = client.get("/t/getting-started", headers=_login(client, "owner@ten_gs.au")).json()
    assert [i["id"] for i in out["items"]] == list(getting_started.ORDER)
    assert out["total"] == 8 and out["complete"] is False
    assert [i["id"] for i in out["items"] if i["done"]] == ["team"]      # the admin is a second member
    admin_ids = [i["id"] for i in client.get("/t/getting-started", headers=_login(client, "pm@hs.au")).json()["items"]]
    assert "team" not in admin_ids and "dpa" not in admin_ids and "setup" in admin_ids


async def test_staff_and_dentists_cannot_open_it(db, client):
    t = await _practice(db)
    await auth_service.create_user(email="desk@hs.au", password=PASSWORD, tenant_id=t.id, role="staff")
    assert client.get("/t/getting-started", headers=_login(client, "desk@hs.au")).status_code == 403


async def test_a_business_without_appointments_has_no_calendar_item(db, client):
    await _practice(db, tenant_id="ten_hotel", vertical="hotel")
    out = client.get("/t/getting-started", headers=_login(client, "owner@ten_hotel.au")).json()
    assert "calendar" not in [i["id"] for i in out["items"]]


async def test_everything_done_is_complete(db):
    items = getting_started.items_for({k: True for k in getting_started.ORDER}, owner=True, scheduling=True)
    assert getting_started.summary(items)["complete"] is True
    assert getting_started.summary([])["complete"] is False


async def test_the_platform_details_page_gets_every_signal(db, client):
    t = await _practice(db)
    await auth_service.create_user(email="root@platform.io", password=PASSWORD, platform_admin=True)
    await Repos(db, t.id).calls.insert_one({"call_id": "test_x", "channel": "browser", "outcome": "completed",
                                            "started_at": datetime.now(timezone.utc)})
    body = client.get(f"/platform/tenants/{t.id}", headers=_login(client, "root@platform.io")).json()
    assert set(body["signals"]) == set(getting_started.ORDER)
    assert body["signals"]["test_call"] is True and body["signals"]["first_call"] is False
