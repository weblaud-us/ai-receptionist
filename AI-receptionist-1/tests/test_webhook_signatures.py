"""Forged Twilio and Meta webhooks must be rejected (Tasks 0.5, 0.6)."""

import json

from twilio.request_validator import RequestValidator

from core.integrations.meta_auth import meta_signature

TWILIO_URL = "https://receptionist.example.com/voice/incoming"
CALL_FORM = {"CallSid": "CA" + "0" * 32, "From": "+8801712345678", "To": "+15550001111"}


def _twilio_sig(url: str, params: dict) -> str:
    return RequestValidator("test-twilio-auth-token").compute_signature(url, params)


# ── Twilio ──────────────────────────────────────────────

def test_twilio_incoming_without_signature_is_403(client):
    r = client.post("/voice/incoming", data=CALL_FORM)
    assert r.status_code == 403


def test_twilio_incoming_with_forged_signature_is_403(client):
    r = client.post("/voice/incoming", data=CALL_FORM, headers={"X-Twilio-Signature": "forged"})
    assert r.status_code == 403


def test_twilio_signature_over_different_params_is_403(client):
    sig = _twilio_sig(TWILIO_URL, {**CALL_FORM, "From": "+15559999999"})
    r = client.post("/voice/incoming", data=CALL_FORM, headers={"X-Twilio-Signature": sig})
    assert r.status_code == 403


def test_twilio_incoming_with_valid_signature_passes(client):
    sig = _twilio_sig(TWILIO_URL, CALL_FORM)
    r = client.post("/voice/incoming", data=CALL_FORM, headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200
    assert "<Stream" in r.text


def test_twilio_status_callback_is_protected(client):
    r = client.post("/voice/status", data={"CallSid": "CA1", "CallStatus": "completed"})
    assert r.status_code == 403


def test_twilio_media_stream_without_signature_is_rejected(client):
    import pytest
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
