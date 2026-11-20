"""
Phase 2 API: auth, roles, and tenant isolation on every endpoint (Roadmap Task 2.3.3).

Two tenants — a salon (A) and a plumber (B) — share phone numbers and look-alike IDs. Every
endpoint called as A must never return or change B's data, and a spoofed X-Tenant-Id is refused.
"""

from datetime import datetime, timedelta, timezone

import pytest

from core.auth import service as auth_service
from core.repositories import Repos
from core.services.tenant_admin import create_tenant
from core.tenancy import invalidate
from core.tenancy.resolver import resolve_by_phone_number

SHARED_PHONE = "+15125550100"
PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "COOKIE_SECURE", False)   # TestClient talks plain http
    auth_service.reset_rate_limits()
    invalidate()
    yield
    invalidate()


async def _login(client, email, password=PASSWORD):
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _h(token, tenant=None):
    h = {"Authorization": f"Bearer {token}"}
    if tenant:
        h["X-Tenant-Id"] = tenant
    return h


@pytest.fixture
async def world(db, client):
    a = await create_tenant(name="Bella Salon", vertical="salon", timezone="America/Chicago", tenant_id="ten_A")
    b = await create_tenant(name="Bob Plumbing", vertical="home_services", timezone="America/Chicago", tenant_id="ten_B")
    for t in (a, b):
        repos = Repos(db, t.id)
        await repos.contacts.insert_one({"_id": f"con_{t.id}", "phone": SHARED_PHONE, "name": f"Customer of {t.name}"})
        await repos.messages.insert_one({"message_id": f"MSG_{t.id}", "caller_name": t.name, "callback_number": SHARED_PHONE,
                                         "message": "secret", "status": "new", "created_at": datetime.now(timezone.utc)})
        await repos.calls.insert_one({"call_id": f"call_{t.id}", "status": "completed", "outcome": "message",
                                      "started_at": datetime.now(timezone.utc), "caller_phone": SHARED_PHONE,
                                      "transcript": [{"role": "user", "text": f"secret {t.id}"}]})
        await repos.leads.insert_one({"_id": f"lead_{t.id}", "name": "x", "phone": SHARED_PHONE, "problem": "p",
                                      "status": "new", "created_at": datetime.now(timezone.utc)})
        await repos.resources.insert_one({"_id": f"res_{t.id}", "name": "Sam", "kind": "staff", "active": True})
    await auth_service.create_user(email="owner@a.com", password=PASSWORD, name="Ann", tenant_id="ten_A", role="owner")
    await auth_service.create_user(email="staff@a.com", password=PASSWORD, tenant_id="ten_A", role="staff")
    await auth_service.create_user(email="owner@b.com", password=PASSWORD, tenant_id="ten_B", role="owner")
    await auth_service.create_user(email="root@platform.io", password=PASSWORD, platform_admin=True)
    return {"a": a, "b": b, "owner_a": (await _login(client, "owner@a.com"))["access_token"],
            "staff_a": (await _login(client, "staff@a.com"))["access_token"],
            "owner_b": (await _login(client, "owner@b.com"))["access_token"],
            "root": (await _login(client, "root@platform.io"))["access_token"]}


# ── Auth ─────────────────────────────────────────────────

async def test_login_refresh_logout(db, client):
    await create_tenant(name="Bella Salon", vertical="salon", tenant_id="ten_A")
    await auth_service.create_user(email="Owner@A.com", password=PASSWORD, tenant_id="ten_A", role="owner")
    body = await _login(client, "owner@a.com")                       # e-mail is case-insensitive
    assert body["tenant_id"] == "ten_A" and body["role"] == "owner" and body["expires_in"] == 15 * 60
    assert body["memberships"][0]["name"] == "Bella Salon"
    assert "rcpt_refresh" in client.cookies
    assert client.get("/auth/me", headers=_h(body["access_token"])).json()["user"]["email"] == "owner@a.com"

    old_cookie = client.cookies["rcpt_refresh"]
    refreshed = client.post("/auth/refresh")
    assert refreshed.status_code == 200 and client.cookies["rcpt_refresh"] != old_cookie   # rotated
    client.cookies.set("rcpt_refresh", old_cookie, path="/auth")
    assert client.post("/auth/refresh").status_code == 401           # the old one is revoked

    client.cookies.clear()
    await _login(client, "owner@a.com")
    assert client.post("/auth/logout").status_code == 200
    assert client.post("/auth/refresh").status_code == 401


async def test_wrong_password_and_rate_limit(db, client):
    await auth_service.create_user(email="u@x.com", password=PASSWORD)
    for _ in range(5):
        assert client.post("/auth/login", json={"email": "u@x.com", "password": "nope-nope-nope"}).status_code == 401
    assert client.post("/auth/login", json={"email": "u@x.com", "password": PASSWORD}).status_code == 429
    assert client.post("/auth/login", json={"email": "ghost@x.com", "password": "whatever12"}).status_code == 401


async def test_password_hash_is_argon2_and_weak_passwords_refused(db):
    user = await auth_service.create_user(email="h@x.com", password=PASSWORD)
    assert user["password_hash"].startswith("$argon2id$")
    with pytest.raises(Exception):
        await auth_service.create_user(email="w@x.com", password="short")


async def test_password_reset_flow(db, client):
    await auth_service.create_user(email="r@x.com", password=PASSWORD)
    token = await auth_service.start_password_reset("r@x.com")
    assert client.post("/auth/password/reset", json={"token": token, "password": "a brand new secret"}).status_code == 200
    assert client.post("/auth/password/reset", json={"token": token, "password": "a brand new secret"}).status_code == 400
    await _login(client, "r@x.com", "a brand new secret")
    assert client.post("/auth/password/forgot", json={"email": "nobody@x.com"}).status_code == 200   # no enumeration


async def test_legacy_env_admin_still_signs_in(db, client, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "env-admin-password")
    body = await _login(client, "admin", "env-admin-password")
    assert body["user"]["is_platform_admin"] and body["role"] == "owner"
    # Legacy single-hotel mode (no tenants): the env admin acts on the .env hotel.
    ctx = client.get("/t/context", headers=_h(body["access_token"])).json()
    assert ctx["legacy"] and ctx["tenant"]["vertical"] == "hotel"


# ── Roles ────────────────────────────────────────────────

async def test_roles(world, client):
    staff, owner = _h(world["staff_a"]), _h(world["owner_a"])
    assert client.get("/t/messages", headers=staff).status_code == 200
    assert client.patch("/t/settings", headers=staff, json={"description": "x"}).status_code == 403
    assert client.post("/t/services", headers=staff, json={"name": "Cut"}).status_code == 403
    assert client.post("/t/team/invite", headers=staff, json={"email": "n@a.com"}).status_code == 403
    assert client.patch("/t/settings", headers=owner, json={"description": "Best cuts in town."}).status_code == 200
    invite = client.post("/t/team/invite", headers=owner, json={"email": "new@a.com", "role": "admin"}).json()
    assert invite["success"] and "accept-invite?token=" in invite["link"]
    assert client.get("/platform/tenants", headers=owner).status_code == 403
    assert client.get("/t/context").status_code == 401


async def test_last_owner_cannot_be_removed(world, client, db):
    owner = _h(world["owner_a"])
    me = (await db.users.find_one({"email": "owner@a.com"}))["_id"]
    assert client.patch(f"/t/team/{me}", headers=owner, json={"role": "staff"}).status_code == 409
    assert client.delete(f"/t/team/{me}", headers=owner).status_code == 409


# ── Isolation ────────────────────────────────────────────

async def test_every_list_endpoint_only_shows_own_tenant(world, client):
    h = _h(world["owner_a"])
    assert [c["id"] for c in client.get("/t/contacts", headers=h).json()["contacts"]] == ["con_ten_A"]
    assert [m["message_id"] for m in client.get("/t/messages", headers=h).json()["messages"]] == ["MSG_ten_A"]
    assert [c["call_id"] for c in client.get("/t/calls", headers=h).json()["calls"]] == ["call_ten_A"]
    assert [x["id"] for x in client.get("/t/leads", headers=h).json()["leads"]] == ["lead_ten_A"]
    assert [x["id"] for x in client.get("/t/resources", headers=h).json()["resources"]] == ["res_ten_A"]
    assert {s["name"] for s in client.get("/t/services", headers=h).json()["services"]} >= {"Haircut"}
    assert all("Estimate" not in s["name"] for s in client.get("/t/services", headers=h).json()["services"])
    # Legacy admin routes are scoped too.
    assert [m["message_id"] for m in client.get("/admin/messages", headers=h).json()["messages"]] == ["MSG_ten_A"]
    assert [c["call_id"] for c in client.get("/admin/calls", headers=h).json()["calls"]] == ["call_ten_A"]


async def test_cannot_read_or_change_other_tenants_records_by_id(world, client, db):
    h = _h(world["owner_a"])
    assert client.get("/t/contacts/con_ten_B", headers=h).status_code == 404
    assert client.patch("/t/contacts/con_ten_B", headers=h, json={"name": "pwned"}).status_code == 404
    assert client.get("/t/calls/call_ten_B", headers=h).status_code == 404
    assert client.get("/admin/calls/call_ten_B", headers=h).status_code == 404
    assert client.patch("/t/messages/MSG_ten_B", headers=h, json={"status": "handled"}).status_code == 404
    assert client.patch("/t/leads/lead_ten_B", headers=h, json={"status": "won"}).status_code == 404
    assert client.patch("/t/resources/res_ten_B", headers=h, json={"name": "x"}).status_code == 404
    assert client.delete("/t/resources/res_ten_B", headers=h).status_code == 404
    assert (await Repos(db, "ten_B").contacts.find_one({"_id": "con_ten_B"}))["name"] == "Customer of Bob Plumbing"
    # A resource from B can't be attached to A's service.
    assert client.post("/t/services", headers=h, json={"name": "X", "resource_ids": ["res_ten_B"]}).status_code == 422


async def test_other_tenants_settings_records_and_hotel_data_are_out_of_reach(world, client, db):
    """The endpoints the first isolation tests didn't reach: knowledge, exceptions, services,
    unanswered questions, appointments, webhooks, team, recordings, exports, hotel bookings and rooms.
    Every call made as A with one of B's ids must miss, and B's data must be untouched."""
    now = datetime.now(timezone.utc)
    rb = Repos(db, "ten_B")
    await rb.knowledge.insert_one({"_id": "kb_B", "question": "Q", "answer": "B's secret answer", "active": True})
    await rb.exceptions.insert_one({"_id": "exc_B", "resource_id": None, "start": now, "end": now + timedelta(hours=1)})
    await rb.unanswered.insert_one({"_id": "unq_B", "question": "B question", "status": "open", "created_at": now})
    await rb.appointments.insert_one({"appointment_id": "APT_B", "status": "confirmed", "start": now + timedelta(days=2),
                                      "end": now + timedelta(days=2, hours=1), "name": "B customer", "phone": SHARED_PHONE})
    await rb.calls.update_one({"call_id": "call_ten_B"}, {"$set": {"recording": {"path": "rec/b.ogg"}}})
    svc_b = (await rb.services.find({}))[0]["_id"]
    await db.tenants.update_one({"_id": "ten_B"}, {"$set": {"integrations.webhooks": [
        {"id": "wh_B", "url": "https://b.example/hook", "events": [], "active": True, "secret": "whsec_b"}]}})
    await db.bookings.insert_one({"tenant_id": "ten_B", "booking_id": "HTLB0001", "status": "confirmed",
                                  "guest_name": "B guest", "guest_phone": SHARED_PHONE})
    await db.rooms.insert_one({"_id": "room_B", "tenant_id": "ten_B", "room_number": "101", "room_type": "deluxe",
                               "is_active": True})
    owner_b_id = (await db.users.find_one({"email": "owner@b.com"}))["_id"]
    invalidate()

    h = _h(world["owner_a"])
    misses = [
        client.patch("/t/knowledge/kb_B", headers=h, json={"answer": "pwned"}),
        client.delete("/t/knowledge/kb_B", headers=h),
        client.delete("/t/exceptions/exc_B", headers=h),
        client.patch(f"/t/services/{svc_b}", headers=h, json={"name": "pwned"}),
        client.delete(f"/t/services/{svc_b}", headers=h),
        client.post("/t/unanswered/unq_B/answer", headers=h, json={"answer": "x"}),
        client.post("/t/unanswered/unq_B/dismiss", headers=h),
        client.patch("/t/appointments/APT_B", headers=h, json={"status": "cancelled"}),
        client.delete("/t/integrations/webhooks/wh_B", headers=h),
        client.post("/t/integrations/webhooks/wh_B/test", headers=h),
        client.patch(f"/t/team/{owner_b_id}", headers=h, json={"role": "staff"}),
        client.get("/t/calls/call_ten_B/recording", headers=h),
        client.get("/bookings/HTLB0001", headers=h),
        client.patch("/bookings/HTLB0001/status", headers=h, params={"status": "checked_in"}),
        client.delete("/bookings/HTLB0001", headers=h),
        client.get("/rooms/room_B", headers=h),
        client.patch("/rooms/room_B", headers=h, json={"room_type": "suite"}),
        client.delete("/rooms/room_B", headers=h),
    ]
    assert [r.status_code for r in misses] == [404] * len(misses), [(r.request.url.path, r.status_code) for r in misses]

    # Lists and exports as A never contain B's rows
    assert "B's secret answer" not in client.get("/t/knowledge", headers=h).text
    assert "B question" not in client.get("/t/unanswered", headers=h).text
    assert "APT_B" not in client.get("/t/appointments", headers=h).text
    assert "wh_B" not in client.get("/t/integrations/webhooks", headers=h).text
    assert "owner@b.com" not in client.get("/t/team", headers=h).text
    assert "HTLB0001" not in client.get("/bookings/", headers=h).text and "room_B" not in client.get("/rooms/", headers=h).text
    contacts_csv = client.get("/t/export/contacts.csv", headers=h)
    assert contacts_csv.status_code == 200 and "Customer of Bob Plumbing" not in contacts_csv.text

    # B's records are exactly as they were
    assert (await rb.knowledge.find_one({"_id": "kb_B"}))["answer"] == "B's secret answer"
    assert await rb.exceptions.find_one({"_id": "exc_B"}) is not None
    assert (await rb.unanswered.find_one({"_id": "unq_B"}))["status"] == "open"
    assert (await rb.appointments.find_one({"appointment_id": "APT_B"}))["status"] == "confirmed"
    assert (await db.bookings.find_one({"booking_id": "HTLB0001"}))["status"] == "confirmed"
    assert (await db.rooms.find_one({"_id": "room_B"}))["room_type"] == "deluxe"
    assert [w["id"] for w in (await db.tenants.find_one({"_id": "ten_B"}))["integrations"]["webhooks"]] == ["wh_B"]


async def test_spoofed_tenant_header_is_refused(world, client):
    assert client.get("/t/messages", headers=_h(world["owner_a"], "ten_B")).status_code == 403
    assert client.get("/admin/messages", headers=_h(world["owner_a"], "ten_B")).status_code == 403
    assert client.get("/bookings/", headers=_h(world["owner_a"], "ten_B")).status_code == 403


async def test_platform_admin_acts_on_a_tenant_with_the_header(world, client):
    root = world["root"]
    assert client.get("/t/messages", headers=_h(root)).status_code == 400            # must pick a tenant
    msgs = client.get("/t/messages", headers=_h(root, "ten_B")).json()["messages"]
    assert [m["message_id"] for m in msgs] == ["MSG_ten_B"]


async def test_a_platform_admin_opening_a_business_is_audited_once_per_hour(world, client, db):
    """Opening another business (impersonation) is recorded in that business's audit log, even
    before the admin changes anything."""
    root = world["root"]
    for _ in range(3):
        assert client.get("/t/messages", headers=_h(root, "ten_B")).status_code == 200
    entries = await db.audit_log.find({"tenant_id": "ten_B", "action": "platform.open_business"}).to_list(10)
    assert len(entries) == 1 and entries[0]["user_id"].startswith("platform:")
    assert entries[0]["diff"]["path"] == "/t/messages"
    # An owner working in their own business isn't "opening" anything
    client.get("/t/messages", headers=_h(world["owner_a"]))
    assert await db.audit_log.count_documents({"tenant_id": "ten_A", "action": "platform.open_business"}) == 0


async def test_settings_update_is_validated_audited_and_applies_to_next_lookup(world, client, db):
    h = _h(world["owner_a"])
    bad = client.patch("/t/settings", headers=h, json={"hours": [{"dow": 9, "open": "9am", "close": "x"}]})
    assert bad.status_code == 422
    assert client.patch("/t/settings", headers=h, json={"status": "active"}).status_code == 422   # platform-only field
    ok = client.patch("/t/settings", headers=h, json={"agent": {"name": "Mia", "greeting": ""}, "transfer": {"number": "+15125550199"}})
    assert ok.status_code == 200 and "this is Mia" in ok.json()["greeting"]
    assert ok.json()["tenant"]["agent"]["voice_provider"] == "elevenlabs"     # nested merge kept other fields
    ctx = client.get("/t/context", headers=h).json()
    assert ctx["tenant"]["agent"]["name"] == "Mia"
    entry = (await db.audit_log.find_one({"tenant_id": "ten_A", "action": "settings.update"}))
    assert entry and "agent" in entry["diff"]


async def test_appointments_through_the_dashboard_are_scoped_and_race_free(world, client):
    h = _h(world["owner_a"])
    svc = client.get("/t/services", headers=h).json()["services"][0]
    # A Wednesday at least 3 days ahead: the salon template is open Tuesday–Saturday, so the test
    # doesn't depend on which weekday it runs.
    start = datetime.now(timezone.utc).date() + timedelta(days=3)
    day = (start + timedelta(days=(2 - start.weekday()) % 7)).isoformat()
    slots = client.get("/t/slots", headers=h, params={"service_id": svc["id"], "day": day}).json()["slots"]
    assert slots
    body = {"service_id": svc["id"], "start": slots[0]["start"], "name": "Walk-in", "phone": SHARED_PHONE}
    assert client.post("/t/appointments", headers=h, json=body).status_code == 200
    assert client.post("/t/appointments", headers=h, json={**body, "resource_id": slots[0]["resource_id"]}).status_code == 409
    listed = client.get("/t/appointments", headers=h, params={"date_from": day, "date_to": day}).json()["appointments"]
    assert len(listed) == 1
    assert client.get("/t/appointments", headers=_h(world["owner_b"]), params={"date_from": day}).json()["appointments"] == []


async def test_knowledge_and_unanswered(world, client, db):
    h = _h(world["owner_a"])
    item = client.post("/t/knowledge", headers=h, json={"question": "Gift cards?", "answer": "Yes, any amount."}).json()
    assert client.get("/t/knowledge", headers=_h(world["owner_b"])).json()["items"] and \
        all(i["id"] != item["id"] for i in client.get("/t/knowledge", headers=_h(world["owner_b"])).json()["items"])
    await Repos(db, "ten_A").unanswered.insert_one({"_id": "un1", "question": "Do you do weddings?", "status": "open"})
    assert client.post("/t/unanswered/un1/answer", headers=_h(world["owner_b"]), json={"answer": "x"}).status_code == 404
    r = client.post("/t/unanswered/un1/answer", headers=h, json={"answer": "Yes, book a trial first."})
    assert r.status_code == 200 and client.get("/t/unanswered", headers=h).json()["items"] == []


# ── Platform ─────────────────────────────────────────────

async def test_platform_provisions_tenant_owner_and_number(world, client, db):
    root = _h(world["root"])
    catalog = client.get("/platform/verticals", headers=root)            # no X-Tenant-Id needed
    assert catalog.status_code == 200 and any(v["key"] == "restaurant" for v in catalog.json()["verticals"])
    assert client.get("/platform/verticals", headers=_h(world["owner_a"])).status_code == 403
    r = client.post("/platform/tenants", headers=root, json={
        "name": "Sunny Diner", "vertical": "restaurant", "timezone": "Europe/London", "country": "GB",
        "currency": "GBP", "owner_email": "chef@sunny.co.uk", "inbound_number": "+44 20 7946 0000"})
    assert r.status_code == 200, r.text
    tid = r.json()["tenant"]["id"]
    assert "reset-password?token=" in r.json()["set_password_link"]
    routed = await resolve_by_phone_number("+442079460000")
    assert routed and routed.id == tid and routed.vertical == "restaurant"
    assert await Repos(db, tid).services.count() == 1                     # restaurant template seeded
    dup = client.post("/platform/phone-numbers", headers=root, json={"e164": "+442079460000", "tenant_id": "ten_A"})
    assert dup.status_code == 409
    assert client.delete("/platform/phone-numbers/+442079460000", headers=root).status_code == 200
    invalidate()
    assert await resolve_by_phone_number("+442079460000") is None
    listed = {t["id"] for t in client.get("/platform/tenants", headers=root).json()["tenants"]}
    assert {"ten_A", "ten_B", tid} <= listed
    assert client.patch(f"/platform/tenants/{tid}", headers=root, json={"status": "suspended"}).status_code == 200
    assert (await db.audit_log.find_one({"tenant_id": tid, "action": "tenant.create"}))


async def test_suspended_tenant_users_are_locked_out(world, client, db):
    await db.tenants.update_one({"_id": "ten_A"}, {"$set": {"status": "suspended"}})
    invalidate()
    # Phase 5: the app shell and billing stay reachable (to pay or export); everything else is locked.
    assert client.get("/t/calls", headers=_h(world["owner_a"])).status_code == 403
    assert client.get("/t/messages", headers=_h(world["owner_a"])).status_code == 403
    assert client.get("/t/context", headers=_h(world["owner_a"])).status_code == 200
    assert client.get("/t/billing", headers=_h(world["owner_a"])).status_code == 200
    assert client.get("/t/calls", headers=_h(world["root"], "ten_A")).status_code == 200


# ── Migration 0003 ───────────────────────────────────────

async def test_hotel_migration_is_idempotent(db, monkeypatch):
    from config import settings
    from migrations.m0003_hotel_to_tenant import migrate

    monkeypatch.setattr(settings, "TWILIO_PHONE_NUMBER", "+8809611000000")
    await db.rooms.insert_one({"_id": "r1", "room_number": "101", "room_type": "standard", "is_active": True})
    await db.bookings.insert_one({"booking_id": "HTL1", "room_id": "r1", "guest_phone": "+8801711111111"})
    await db.guests.insert_one({"phone": "+8801711111111", "name": "Rahim", "vip_tier": "gold", "total_stays": 2})
    await db.messages.insert_one({"message_id": "MSG1", "tenant_id": "default", "status": "new"})
    await db.calls.insert_one({"call_id": "c1", "tenant_id": "default", "started_at": datetime.now(timezone.utc)})

    tid = await migrate(dry_run=True)
    assert await db.tenants.count_documents({}) == 0 and await db.rooms.find_one({"tenant_id": {"$exists": True}}) is None

    tid = await migrate(owner_email="owner@hotel.com", owner_name="Owner")
    tenant = await db.tenants.find_one({"_id": tid})
    assert tenant["vertical"] == "hotel" and tenant["name"] == "Grand Azure Hotel"
    for coll in ("rooms", "bookings", "guests", "messages", "calls"):
        assert await db[coll].count_documents({"tenant_id": tid}) == 1, coll
    contact = await db.contacts.find_one({"tenant_id": tid})
    assert contact["name"] == "Rahim" and contact["tags"] == ["gold"]
    assert (await db.users.find_one({"email": "owner@hotel.com"}))["memberships"] == [{"tenant_id": tid, "role": "owner"}]
    invalidate()
    assert (await resolve_by_phone_number("+8809611000000")).id == tid

    again = await migrate(owner_email="owner@hotel.com")
    assert again == tid and await db.tenants.count_documents({}) == 1 and await db.contacts.count_documents({}) == 1


async def test_hotel_tenant_routes_after_migration(db, client, monkeypatch):
    from migrations.m0003_hotel_to_tenant import migrate

    await db.rooms.insert_one({"_id": "r1", "room_number": "101", "room_type": "standard", "is_active": True})
    tid = await migrate(owner_email="owner@hotel.com")
    await db.users.update_one({"email": "owner@hotel.com"}, {"$set": {
        "password_hash": __import__("core.auth.passwords", fromlist=["x"]).hash_password(PASSWORD)}})
    other = await create_tenant(name="Other Inn", vertical="hotel", tenant_id="ten_inn")
    await Repos(db, other.id).rooms.insert_one({"_id": "r2", "room_number": "101", "room_type": "suite", "is_active": True})
    token = (await _login(client, "owner@hotel.com"))["access_token"]
    rooms = client.get("/rooms/", headers=_h(token)).json()["rooms"]
    assert [r["id"] for r in rooms] == ["r1"] and tid
    assert client.get("/rooms/r2", headers=_h(token)).status_code == 404


def test_call_forwarding_guide_follows_the_business_country():
    from routers.tenant import forwarding_guide_for

    au = [r["carrier"] for r in forwarding_guide_for("AU", "+61291234567")]
    assert any("Telstra landline" in c for c in au) and not any("Verizon" in c for c in au)
    gb = forwarding_guide_for("GB", "+442071234567")
    assert any("BT landline" in r["carrier"] for r in gb) and all("countries" not in r for r in gb)
    assert "+442071234567" in gb[0]["no_answer"]
    assert len(forwarding_guide_for("ZZ", "")) > 3                       # unknown country: the full list
    assert any("Verizon" in r["carrier"] for r in forwarding_guide_for("US", "+15125550100"))


# ── Dental vertical fields ───────────────────────────────

async def test_service_value_and_category_round_trip_and_dental_settings(world, client):
    h = _h(world["owner_a"])
    r = client.post("/t/services", headers=h, json={"name": "Implant consultation", "category": "implant",
                                                    "estimated_value_minor": 450000})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    assert client.patch(f"/t/services/{sid}", headers=h, json={"estimated_value_minor": 500000}).status_code == 200
    row = next(s for s in client.get("/t/services", headers=h).json()["services"] if s["id"] == sid)
    assert row["category"] == "implant" and row["estimated_value_minor"] == 500000
    assert client.post("/t/services", headers=h, json={"name": "X", "category": "spaceship"}).status_code == 422
    assert client.post("/t/services", headers=h, json={"name": "X", "estimated_value_minor": -1}).status_code == 422

    r = client.patch("/t/settings", headers=h, json={"vertical": "dental", "country": "GB",
                                                     "dental": {"accepts_nhs": True}})
    assert r.status_code == 200, r.text
    ctx = client.get("/t/context", headers=h).json()
    assert ctx["vertical"]["key"] == "dental" and ctx["tenant"]["dental"]["accepts_nhs"] is True
    assert ctx["tenant"]["dental"]["accepts_new_nhs_patients"] is False
