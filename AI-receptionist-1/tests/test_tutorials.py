"""Tutorial progress per user: welcome tours and getting-started lists finished or skipped (/me/tutorials)."""

import asyncio

import pytest

from config import settings
from core.auth import service as auth_service
from core.auth import tutorials
from core.services.tenant_admin import create_tenant
from core.tenancy import invalidate

PASSWORD = "correct horse battery"
ADMIN_KEY = "admin-key-0123456789abcdef0123456789abcdef"


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


async def _people(db):
    t = await create_tenant(name="Harbour Smiles", vertical="dental", country="AU", timezone="Australia/Sydney",
                            tenant_id="ten_tut", status="active", seed_template=False)
    owner = await auth_service.create_user(email="owner@hs.au", password=PASSWORD, tenant_id=t.id, role="owner")
    desk = await auth_service.create_user(email="desk@hs.au", password=PASSWORD, tenant_id=t.id, role="staff")
    return t, owner, desk


async def test_a_new_user_has_seen_nothing_then_done_and_reset_round_trip(db, client):
    await _people(db)
    h = _login(client, "owner@hs.au")
    assert client.get("/me/tutorials", headers=h).json() == {"tutorials": {}, "persist": True}

    out = client.put("/me/tutorials/tour:dashboard:owner", json={"status": "done"}, headers=h).json()
    assert out["tutorials"]["tour:dashboard:owner"]["status"] == "done"
    assert out["tutorials"]["tour:dashboard:owner"]["at"]

    client.put("/me/tutorials/checklist:dashboard:getting_started:ten_tut", json={"status": "skipped"}, headers=h)
    saved = client.get("/me/tutorials", headers=h).json()["tutorials"]
    assert set(saved) == {"tour:dashboard:owner", "checklist:dashboard:getting_started:ten_tut"}

    out = client.delete("/me/tutorials/checklist:dashboard:getting_started:ten_tut", headers=h).json()
    assert set(out["tutorials"]) == {"tour:dashboard:owner"}


async def test_bad_keys_and_statuses_are_refused(db, client):
    await _people(db)
    h = _login(client, "owner@hs.au")
    for key in ("tour:elsewhere:owner", "tour:dashboard:Owner", "tutorials.x", "tour:dashboard:owner:a.b",
                "checklist:dashboard:x:" + "a" * 65):
        assert client.put(f"/me/tutorials/{key}", json={"status": "done"}, headers=h).status_code == 422, key
    assert client.put("/me/tutorials/tour:dashboard:owner", json={"status": "seen"}, headers=h).status_code == 422


async def test_each_person_has_their_own_progress(db, client):
    await _people(db)
    owner, desk = _login(client, "owner@hs.au"), _login(client, "desk@hs.au")
    client.put("/me/tutorials/tour:dashboard:owner", json={"status": "done"}, headers=owner)
    assert client.get("/me/tutorials", headers=desk).json()["tutorials"] == {}


async def test_new_keys_stop_at_the_cap_but_saved_ones_can_still_change(db, client):
    _, owner, _ = await _people(db)
    for i in range(tutorials.MAX_KEYS):
        await tutorials.mark(owner["_id"], f"checklist:dashboard:getting_started:ten_{i}", "done")
    h = _login(client, "owner@hs.au")
    assert client.put("/me/tutorials/checklist:dashboard:getting_started:ten_new", json={"status": "done"}, headers=h).status_code == 409
    r = client.put("/me/tutorials/checklist:dashboard:getting_started:ten_3", json={"status": "skipped"}, headers=h)
    assert r.status_code == 200
    assert r.json()["tutorials"]["checklist:dashboard:getting_started:ten_3"]["status"] == "skipped"


async def test_writes_racing_at_the_cap_never_go_over_it(db):
    _, owner, _ = await _people(db)
    for i in range(tutorials.MAX_KEYS - 1):
        await tutorials.mark(owner["_id"], f"checklist:dashboard:getting_started:ten_{i}", "done")
    results = await asyncio.gather(*(tutorials.mark(owner["_id"], f"tour:dashboard:race:x{i}", "done")
                                     for i in range(5)), return_exceptions=True)
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert len(await tutorials.get(owner["_id"])) == tutorials.MAX_KEYS


async def test_a_disabled_account_cannot_save(db):
    _, owner, _ = await _people(db)
    await db.users.update_one({"_id": owner["_id"]}, {"$set": {"disabled": True}})
    with pytest.raises(Exception) as e:
        await tutorials.mark(owner["_id"], "tour:dashboard:owner", "done")
    assert getattr(e.value, "status_code", None) == 404


def test_the_admin_key_gets_an_empty_map_that_is_not_saved(client, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_SECRET_KEY", ADMIN_KEY)
    h = {"X-Admin-Key": ADMIN_KEY}
    assert client.get("/me/tutorials", headers=h).json() == {"tutorials": {}, "persist": False}
    r = client.put("/me/tutorials/tour:platform:admin", json={"status": "done"}, headers=h)
    assert r.status_code == 200 and r.json() == {"tutorials": {}, "persist": False}


def test_signed_out_is_refused(client):
    assert client.get("/me/tutorials").status_code == 401
    assert client.put("/me/tutorials/tour:dashboard:owner", json={"status": "done"}).status_code == 401
