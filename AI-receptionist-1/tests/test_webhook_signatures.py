"""Forged Twilio and Meta webhooks must be rejected (Tasks 0.5, 0.6)."""

import json

from twilio.request_validator import RequestValidator

from core.integrations.meta_auth import meta_signature

import pytest

from config import settings

BASE = "https://receptionist.example.com"
TWILIO_URL = f"{BASE}/voice/incoming"
FALLBACK_URL = f"{BASE}/telephony/fallback"
CALL_FORM = {"CallSid": "CA" + "0" * 32, "From": "+8801712345678", "To": "+15550001111"}

# The old Media Streams path (/voice/*) exists only while LEGACY_TWILIO_VOICE is on (Task 1.13).
legacy_only = pytest.mark.skipif(not settings.LEGACY_TWILIO_VOICE, reason="legacy /voice/* routes are off")


def _twilio_sig(url: str, params: dict) -> str:
    return RequestValidator("test-twilio-auth-token").compute_signature(url, params)


# ── Twilio (endpoints that stay after the SIP cutover) ──

def test_twilio_fallback_without_signature_is_403(client):
    assert client.post("/telephony/fallback", data=CALL_FORM).status_code == 403


def test_twilio_fallback_with_forged_signature_is_403(client):
    r = client.post("/telephony/fallback", data=CALL_FORM, headers={"X-Twilio-Signature": "forged"})
    assert r.status_code == 403


def test_twilio_signature_over_different_params_is_403(client):
    sig = _twilio_sig(FALLBACK_URL, {**CALL_FORM, "From": "+15559999999"})
    r = client.post("/telephony/fallback", data=CALL_FORM, headers={"X-Twilio-Signature": sig})
    assert r.status_code == 403


def test_twilio_fallback_with_valid_signature_passes(client):
    sig = _twilio_sig(FALLBACK_URL, CALL_FORM)
    r = client.post("/telephony/fallback", data=CALL_FORM, headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200 and "<Response>" in r.text


def test_twilio_inbound_sms_is_protected(client):
    assert client.post("/telephony/sms/inbound", data={"From": "+15125550100", "Body": "STOP"}).status_code == 403


def test_legacy_voice_routes_are_gone_when_the_flag_is_off(client):
    if settings.LEGACY_TWILIO_VOICE:
        pytest.skip("LEGACY_TWILIO_VOICE is on in this environment")
    assert client.post("/voice/incoming", data=CALL_FORM).status_code == 404
    assert client.post("/voice/outbound", json={}).status_code == 404


# ── Twilio, old /voice/* path (only while LEGACY_TWILIO_VOICE is on) ──

@legacy_only
def test_twilio_incoming_without_signature_is_403(client):
    assert client.post("/voice/incoming", data=CALL_FORM).status_code == 403


@legacy_only
def test_twilio_incoming_with_valid_signature_passes(client):
    sig = _twilio_sig(TWILIO_URL, CALL_FORM)
    r = client.post("/voice/incoming", data=CALL_FORM, headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200
    assert "<Stream" in r.text


@legacy_only
def test_twilio_status_callback_is_protected(client):
    r = client.post("/voice/status", data={"CallSid": "CA1", "CallStatus": "completed"})
    assert r.status_code == 403


@legacy_only
def test_twilio_media_stream_without_signature_is_rejected(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/voice/stream"):
            pass


# ── Meta WhatsApp ───────────────────────────────────────

PAYLOAD = json.dumps({"object": "whatsapp_business_account", "entry": []}).encode()


def test_meta_webhook_with_valid_signature_passes(client):
    sig = meta_signature("test-meta-app-secret", PAYLOAD)
    r = client.post("/webhook", content=PAYLOAD, headers={"X-Hub-Signature-256": sig,
                                                          "Content-Type": "application/json"})
    assert r.status_code == 200


def test_meta_webhook_with_tampered_body_is_403(client):
    sig = meta_signature("test-meta-app-secret", PAYLOAD)
    tampered = PAYLOAD.replace(b"[]", b"[{}]")
    r = client.post("/webhook", content=tampered, headers={"X-Hub-Signature-256": sig,
                                                           "Content-Type": "application/json"})
    assert r.status_code == 403


def test_meta_webhook_without_signature_is_403(client):
    r = client.post("/webhook", content=PAYLOAD, headers={"Content-Type": "application/json"})
    assert r.status_code == 403


def test_meta_webhook_signed_with_verify_token_is_403(client):
    """The verify token is not the signing key — only the app secret is."""
    sig = meta_signature("test-verify-token", PAYLOAD)
    r = client.post("/webhook", content=PAYLOAD, headers={"X-Hub-Signature-256": sig})
    assert r.status_code == 403


# ── WhatsApp voice replies are real MP3 (not the phone line's μ-law) ─────────

async def test_whatsapp_voice_reply_uses_mp3_not_phone_audio(monkeypatch):
    import whatsapp.meta_handler as mh

    class FakeTTS:
        async def synthesize(self, text):            # phone line: 8 kHz μ-law
            raise AssertionError("WhatsApp must not get μ-law audio")

        async def synthesize_mp3(self, text):
            return b"ID3fake-mp3"

    monkeypatch.setattr(mh, "get_tts", lambda: FakeTTS())
    assert await mh._synthesize_tts_mp3("Hello") == b"ID3fake-mp3"
    assert await mh._synthesize_tts_mp3("") is None
