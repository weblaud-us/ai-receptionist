"""/livekit/token needs a valid, unexpired signed link; the open send-whatsapp endpoint is gone (Task 0.7)."""

import time

from core.signed_links import build_call_link, sign_call_room, verify_call_room

ROOM = "wa_0123456789abcdef"


def test_token_without_signature_is_403(client):
    assert client.get(f"/livekit/token?room={ROOM}").status_code == 403


def test_token_with_forged_signature_is_403(client):
    exp, _ = sign_call_room(ROOM)
    assert client.get(f"/livekit/token?room={ROOM}&exp={exp}&sig={'0' * 32}").status_code == 403


def test_token_for_a_different_room_is_403(client):
    exp, sig = sign_call_room(ROOM)
    assert client.get(f"/livekit/token?room=wa_other&exp={exp}&sig={sig}").status_code == 403


def test_expired_link_is_403(client):
    exp, sig = sign_call_room(ROOM, now=time.time() - 3600)
    assert client.get(f"/livekit/token?room={ROOM}&exp={exp}&sig={sig}").status_code == 403


def test_valid_link_gets_a_token(client):
    exp, sig = sign_call_room(ROOM)
    r = client.get(f"/livekit/token?room={ROOM}&exp={exp}&sig={sig}")
    assert r.status_code == 200
    body = r.json()
    assert body["room"] == ROOM and body["token"]
    assert "call_url" not in body


def test_token_dispatches_the_receptionist(client):
    """The worker uses an explicit agent name, so the token itself must request the agent."""
    from livekit import api

    exp, sig = sign_call_room(ROOM)
    token = client.get(f"/livekit/token?room={ROOM}&exp={exp}&sig={sig}").json()["token"]
    claims = api.TokenVerifier("APItestkey", "test-livekit-secret-0123456789abcdef").verify(token)
    assert [a.agent_name for a in claims.room_config.agents] == ["receptionist"]
    assert claims.video.room == ROOM


def test_built_link_verifies():
    link = build_call_link("https://x.example.com/", ROOM)
    query = dict(p.split("=") for p in link.split("?", 1)[1].split("&"))
    assert link.startswith(f"https://x.example.com/call/{ROOM}?")
    assert verify_call_room(ROOM, int(query["exp"]), query["sig"])


def test_send_whatsapp_endpoint_removed(client):
    r = client.post("/livekit/send-whatsapp", json={"to": "+15550001111", "message": "spam"})
    assert r.status_code in (404, 405)
