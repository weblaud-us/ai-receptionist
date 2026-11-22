"""Staff → patient WhatsApp from the patient profile: 24-hour window, templates, Meta's real errors,
delivery receipts, do-not-contact and tenant isolation."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

import core.integrations.whatsapp as wa
from core.auth import service as auth_service
from core.repositories import Repos
from core.services import whatsapp_outbox as outbox
from core.services.tenant_admin import create_tenant
from core.tenancy import invalidate

PASSWORD = "correct horse battery"
PHONE = "+61412345678"
TEMPLATES = [{"name": "appointment_reminder", "language": "en", "category": "UTILITY",
              "body": "Hi {{1}}, a reminder of your visit on {{2}}.", "params": 2}]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    monkeypatch.setattr(settings, "META_WA_ACCESS_TOKEN", "tok")
    monkeypatch.setattr(settings, "META_WA_PHONE_NUMBER_ID", "PN_PLATFORM")
    monkeypatch.setattr(settings, "META_WABA_ID", "WABA")
    auth_service.reset_rate_limits()
    invalidate()
    yield
    invalidate()


@pytest.fixture
def sent(monkeypatch):
    calls = []

    async def fake_send(to, payload, phone_number_id=None):
        calls.append({"to": to, "payload": payload, "pnid": phone_number_id})
        return wa.SendResult(True, message_id=f"wamid.{len(calls)}")

    async def fake_templates(waba_id=None, *, fresh=False):
        return TEMPLATES

    monkeypatch.setattr(wa, "send_message", fake_send)
    monkeypatch.setattr(wa, "list_templates", fake_templates)
    return calls


@pytest.fixture
async def practice(db, client):
    a = await create_tenant(name="Bright Dental", vertical="dental", timezone="Australia/Sydney", tenant_id="ten_A",
                            extra={"integrations": {"whatsapp": {"phone_number_id": "PN_A"}}})
    await create_tenant(name="Other", vertical="dental", tenant_id="ten_B")
    await Repos(db, "ten_A").contacts.insert_one({"_id": "con_A", "phone": PHONE, "name": "Mia", "tags": [],
                                                  "do_not_contact": False})
    await Repos(db, "ten_B").contacts.insert_one({"_id": "con_B", "phone": PHONE, "name": "B", "tags": [],
                                                  "do_not_contact": False})
    await auth_service.create_user(email="staff@a.com", password=PASSWORD, tenant_id="ten_A", role="staff")
    tok = client.post("/auth/login", json={"email": "staff@a.com", "password": PASSWORD}).json()["access_token"]
    return {"tenant": a, "h": {"Authorization": f"Bearer {tok}"}}


def _resp(status, body):
    return httpx.Response(status, json=body, request=httpx.Request("POST", "https://graph.facebook.com/x"))


def test_meta_errors_become_plain_english():
    code, msg = wa.parse_error(_resp(400, {"error": {"code": 131047, "message": "Re-engagement message"}}))
    assert code == 131047 and "24 hours" in msg
    code, msg = wa.parse_error(_resp(401, {"error": {"code": 190, "message": "Session has expired"}}))
    assert code == 190 and "access token" in msg
    code, msg = wa.parse_error(_resp(400, {"error": {"code": 999, "error_data": {"details": "odd thing"}}}))
    assert code == 999 and "odd thing" in msg
    assert outbox.fill_template("Hi {{1}}, see you {{ 2 }}", ["Mia", "Tue"]) == "Hi Mia, see you Tue"


async def test_free_text_needs_the_24h_window(practice, client, db, sent):
    h = practice["h"]
    state = client.get("/t/contacts/con_A/whatsapp", headers=h).json()
    assert state["window"]["open"] is False and state["messages"] == []
    r = client.post("/t/contacts/con_A/whatsapp", headers=h, json={"text": "Hello"})
    assert r.status_code == 409 and "template" in r.json()["detail"] and sent == []

    await outbox.note_inbound(Repos(db, "ten_A"), PHONE)                       # the patient messaged us
    assert client.get("/t/contacts/con_A/whatsapp", headers=h).json()["window"]["open"] is True
    r = client.post("/t/contacts/con_A/whatsapp", headers=h, json={"text": "Your crown is ready."})
    assert r.status_code == 200, r.text
    assert sent[0]["pnid"] == "PN_A" and sent[0]["to"] == PHONE and sent[0]["payload"]["type"] == "text"
    msgs = client.get("/t/contacts/con_A/whatsapp", headers=h).json()["messages"]
    assert msgs[0]["body"] == "Your crown is ready." and msgs[0]["status"] == "accepted"


async def test_window_closes_after_24_hours(db):
    old = datetime.now(timezone.utc) - timedelta(hours=25)
    assert outbox.window({"wa_last_inbound_at": old})["open"] is False
    assert outbox.window({"wa_last_inbound_at": old.replace(tzinfo=None) + timedelta(hours=2)})["open"] is True


async def test_template_send_and_validation(practice, client, sent):
    h = practice["h"]
    base = {"template": "appointment_reminder", "language": "en"}
    assert client.post("/t/contacts/con_A/whatsapp", headers=h, json={**base, "params": ["Mia"]}).status_code == 422
    assert client.post("/t/contacts/con_A/whatsapp", headers=h, json={**base, "params": ["Mia", " "]}).status_code == 422
    r = client.post("/t/contacts/con_A/whatsapp", headers=h, json={**base, "params": ["Mia", "Tuesday 10am"]})
    assert r.status_code == 200, r.text
    tpl = sent[0]["payload"]["template"]
    assert tpl["name"] == "appointment_reminder" and tpl["language"] == {"code": "en"}
    assert [p["text"] for p in tpl["components"][0]["parameters"]] == ["Mia", "Tuesday 10am"]
    assert r.json()["message"]["body"] == "Hi Mia, a reminder of your visit on Tuesday 10am."
    assert client.get("/t/whatsapp/templates", headers=h).json()["templates"] == TEMPLATES
    assert client.get("/t/whatsapp/status", headers=h).json() == {"configured": True, "own_number": True,
                                                                  "templates_configured": True}


async def test_meta_rejection_is_shown_and_recorded(practice, client, monkeypatch):
    async def reject(to, payload, phone_number_id=None):
        return wa.SendResult(False, error_code=131030, error=wa.explain(131030))

    monkeypatch.setattr(wa, "send_message", reject)
    async def no_templates(waba_id=None, *, fresh=False):
        return []
    monkeypatch.setattr(wa, "list_templates", no_templates)
    h = practice["h"]
    r = client.post("/t/contacts/con_A/whatsapp", headers=h, json={"template": "hello_world", "language": "en_US"})
    assert r.status_code == 502 and "allowed recipient list" in r.json()["detail"]
    row = client.get("/t/contacts/con_A/whatsapp", headers=h).json()["messages"][0]
    assert row["status"] == "failed" and row["error_code"] == 131030


async def test_delivery_receipts_update_the_outbox(practice, client, db, sent):
    h = practice["h"]
    client.post("/t/contacts/con_A/whatsapp", headers=h,
                json={"template": "appointment_reminder", "language": "en", "params": ["Mia", "Tue"]})
    repos = Repos(db, "ten_A")
    assert await outbox.apply_statuses(repos, [{"id": "wamid.1", "status": "delivered"}]) == 1
    assert await outbox.apply_statuses(repos, [{"id": "wamid.1", "status": "sent"}]) == 0      # never backwards
    assert client.get("/t/contacts/con_A/whatsapp", headers=h).json()["messages"][0]["status"] == "delivered"
    await outbox.apply_statuses(repos, [{"id": "wamid.1", "status": "failed",
                                         "errors": [{"code": 131047, "title": "Re-engagement message"}]}])
    row = client.get("/t/contacts/con_A/whatsapp", headers=h).json()["messages"][0]
    assert row["status"] == "failed" and "24 hours" in row["error"]


async def test_webhook_records_inbound_and_statuses(practice, db, sent, monkeypatch):
    from whatsapp import meta_handler

    async def no_reply(**kw):
        return None

    monkeypatch.setattr(meta_handler, "_process_incoming_message", no_reply)
    await Repos(db, "ten_A").wa_outbox.insert_one({"_id": "wa_1", "message_id": "wamid.X", "status": "accepted",
                                                   "contact_id": "con_A", "to": PHONE})
    await meta_handler._handle_webhook_payload({"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "PN_A"},
        "contacts": [{"profile": {"name": "Mia W"}}],
        "messages": [{"id": "in.1", "from": "61412345678", "type": "text", "text": {"body": "hi"}}],
        "statuses": [{"id": "wamid.X", "status": "read"}],
    }}]}]})
    repos = Repos(db, "ten_A")
    assert outbox.window(await repos.contacts.find_one({"_id": "con_A"}))["open"] is True
    assert (await repos.wa_outbox.find_one({"_id": "wa_1"}))["status"] == "read"


async def test_do_not_contact_and_isolation(practice, client, db, sent):
    h = practice["h"]
    assert client.get("/t/contacts/con_B/whatsapp", headers=h).status_code == 404
    assert client.post("/t/contacts/con_B/whatsapp", headers=h, json={"text": "x"}).status_code == 404
    await Repos(db, "ten_A").contacts.update_one({"_id": "con_A"}, {"$set": {"do_not_contact": True}})
    r = client.post("/t/contacts/con_A/whatsapp", headers=h,
                    json={"template": "appointment_reminder", "language": "en", "params": ["a", "b"]})
    assert r.status_code == 409 and sent == []
