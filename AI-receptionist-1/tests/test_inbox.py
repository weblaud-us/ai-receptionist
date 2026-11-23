"""Plan 4.1: one front-desk inbox over messages, leads, escalations and missed confirmations."""

from datetime import datetime, timedelta, timezone

import pytest

from config import settings
from core.auth import service as auth_service
from core.repositories import Repos
from core.services.tenant_admin import create_tenant
from core.tenancy import invalidate

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


async def _practice(db):
    t = await create_tenant(name="Harbour Smiles", vertical="dental", country="AU", timezone="Australia/Sydney",
                            tenant_id="ten_inbox", status="active", seed_template=False)
    repos = Repos(db, t.id)
    now = datetime.now(timezone.utc)
    await repos.messages.insert_one({"_id": "m1", "message_id": "m1", "caller_name": "Sam", "callback_number": "+61400000001",
                                     "message": "Call me about my bill", "urgent": True, "status": "new",
                                     "created_at": now - timedelta(hours=3)})
    await repos.leads.insert_one({"_id": "l1", "kind": "cosmetic_consult", "name": "Kim", "phone": "+61400000002",
                                  "treatment_category": "veneers", "status": "new", "created_at": now})
    await repos.escalations.insert_one({"_id": "e1", "lead_id": "l_x", "headline": "Urgent dental problem",
                                        "status": "open", "created_at": now, "step": 0})
    await repos.appointments.insert_one({"_id": "a1", "appointment_id": "a1", "status": "confirmed", "name": "Mia",
                                         "phone": "+61400000003", "start": now + timedelta(days=1),
                                         "confirmation": {"status": "no_answer"}})
    owner = await auth_service.create_user(email="owner@hs.au", password=PASSWORD, tenant_id=t.id, role="owner")
    desk = await auth_service.create_user(email="desk@hs.au", password=PASSWORD, tenant_id=t.id, role="staff")
    return t, owner, desk


async def test_the_inbox_lists_every_follow_up_with_the_overdue_first(db, client):
    await _practice(db)
    h = _login(client, "owner@hs.au")
    out = client.get("/t/inbox", headers=h).json()
    kinds = [i["kind"] for i in out["items"]]
    assert sorted(kinds) == ["confirmation", "escalation", "lead", "message"]
    assert kinds[0] in ("message", "escalation") and out["items"][0]["overdue"]     # urgent, past due
    assert out["unassigned"] == 4


async def test_assign_then_done_writes_to_the_original_record(db, client):
    t, owner, desk = await _practice(db)
    h = _login(client, "owner@hs.au")
    assert client.patch("/t/inbox/message/m1", json={"assignee_id": desk["_id"]}, headers=h).status_code == 200
    item = next(i for i in client.get("/t/inbox", headers=h).json()["items"] if i["id"] == "m1")
    assert item["assignee_id"] == desk["_id"]
    assert client.patch("/t/inbox/message/m1", json={"done": True}, headers=h).status_code == 200
    msg = await Repos(db, t.id).messages.find_one({"message_id": "m1"})
    assert msg["status"] == "handled" and msg["assignee_id"] == desk["_id"]
    assert client.patch("/t/inbox/confirmation/a1", json={"done": True}, headers=h).status_code == 200
    assert client.patch("/t/inbox/escalation/e1", json={"done": True}, headers=h).status_code == 200
    assert (await Repos(db, t.id).escalations.find_one({"_id": "e1"}))["status"] == "acknowledged"
    left = [i["kind"] for i in client.get("/t/inbox", headers=h).json()["items"]]
    assert left == ["lead"]


async def test_only_team_members_can_be_assigned_and_dentists_dont_see_the_inbox(db, client):
    t, _, _ = await _practice(db)
    h = _login(client, "owner@hs.au")
    assert client.patch("/t/inbox/lead/l1", json={"assignee_id": "usr_stranger"}, headers=h).status_code == 422
    assert client.patch("/t/inbox/nope/x", json={"done": True}, headers=h).status_code == 404
    await Repos(db, t.id).resources.insert_one({"_id": "res_lee", "name": "Dr Lee", "active": True})
    await auth_service.create_user(email="lee@hs.au", password=PASSWORD, tenant_id=t.id, role="dentist",
                                   resource_id="res_lee")
    assert client.get("/t/inbox", headers=_login(client, "lee@hs.au")).status_code == 403
