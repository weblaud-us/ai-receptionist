"""Part C: the platform sends no SMS (config.SMS_ENABLED=False). Nothing texts, and nothing urgent is lost."""

import httpx
import pytest

from config import settings
from core import notifications
from core.integrations import email as email_api
from core.integrations import sms as sms_api
from core.integrations import whatsapp as wa_api
from core.services import reactivation, recovery
from core.tenancy.models import TenantConfig


@pytest.fixture(autouse=True)
def _twilio_configured(monkeypatch):
    # Twilio credentials present: SMS must still not go out, because the platform switch is off.
    monkeypatch.setattr(settings, "SMS_ENABLED", False)
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setattr(settings, "TWILIO_SMS_FROM", "+61400000000")


def _tenant(**kw) -> TenantConfig:
    base = {"_id": "ten_sms", "name": "Harbour Smiles", "slug": "hs", "vertical": "dental", "country": "AU",
            "timezone": "Australia/Sydney", "status": "active"}
    base.update(kw)
    return TenantConfig.from_doc(base)


def test_sms_is_off_by_default():
    assert type(settings).model_fields["SMS_ENABLED"].default is False


async def test_no_text_leaves_even_with_twilio_configured(monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("SMS must not reach Twilio")

    monkeypatch.setattr(httpx.AsyncClient, "post", no_network)
    assert sms_api.configured() is False
    assert await sms_api.send_sms("+61412345678", "hello") is False
    assert await sms_api.send_sms_sid("+61412345678", "hello") is None


async def test_patient_messages_go_by_whatsapp_or_not_at_all(monkeypatch):
    sent: list = []

    async def fake_sms(*a, **k):
        sent.append(("sms", a))
        return True

    async def wa_down(*a, **k):
        return False

    monkeypatch.setattr(sms_api, "send_sms", fake_sms)
    monkeypatch.setattr(wa_api, "configured", lambda pnid=None: True)
    monkeypatch.setattr(wa_api, "send_text", wa_down)
    ok = await notifications.notify_contact(_tenant(), "+61412345678", "Your appointment is booked", prefer="sms")
    assert ok is False and sent == []                       # WhatsApp failed, and there is no SMS fallback


async def test_an_urgent_alert_falls_back_to_email_when_only_sms_was_chosen(monkeypatch):
    mails: list = []

    async def fake_email(to, subject, text):
        mails.append((to, subject))
        return True

    monkeypatch.setattr(email_api, "send_email", fake_email)
    t = _tenant(notifications={"owner_phone": "+61400000001", "owner_email": "dr@hs.au",
                               "on_message": ["sms"], "on_urgent": ["sms", "call"]})
    out = await notifications.notify_owner(t, "on_message", "EMERGENCY: facial swelling", urgent=True)
    assert out.get("email") is True and "sms" not in out
    assert mails and mails[0][0] == "dr@hs.au" and mails[0][1].startswith("URGENT")


def test_missed_call_texts_and_sms_recall_are_off():
    t = _tenant(missed_call_recovery={"enabled": True})
    assert recovery.wants_recovery(t, channel="phone", direction="inbound", caller_phone="+61412345678",
                                   outcome="completed", ended_reason="caller_hangup") is False
    assert reactivation.available(t) is False


async def test_phone_verification_says_why_it_cant_text(db, client, monkeypatch):
    from core.auth import service as auth_service
    from core.services.tenant_admin import create_tenant

    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    monkeypatch.setattr(settings, "APP_ENV", "staging")
    auth_service.reset_rate_limits()
    await create_tenant(name="Harbour Smiles", vertical="dental", country="AU", timezone="Australia/Sydney",
                        tenant_id="ten_sms_api", seed_template=False)
    await auth_service.create_user(email="o@hs.au", password="correct horse battery", tenant_id="ten_sms_api",
                                   role="owner")
    token = client.post("/auth/login", json={"email": "o@hs.au", "password": "correct horse battery"}).json()
    h = {"Authorization": f"Bearer {token['access_token']}"}
    assert client.get("/t/context", headers=h).json()["sms_enabled"] is False
    r = client.post("/t/billing/verify-phone", json={"phone": "+61412345678"}, headers=h)
    assert r.status_code == 503 and "Contact support" in r.json()["detail"]


async def test_an_owner_who_chose_sms_gets_email_instead(monkeypatch):
    mails: list = []

    async def fake_email(to, subject, text):
        mails.append(subject)
        return True

    monkeypatch.setattr(email_api, "send_email", fake_email)
    t = _tenant(notifications={"owner_email": "dr@hs.au", "owner_phone": "+61400000001"})   # defaults: on_message = sms
    out = await notifications.notify_owner(t, "on_message", "New message from Sam")
    assert out == {"email": True} and mails
