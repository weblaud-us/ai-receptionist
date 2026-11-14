"""Dashboard API for voice-agent calls and caller messages."""

from datetime import datetime, timedelta, timezone

import pytest

KEY = "admin-key-0123456789abcdef0123456789abcdef"


@pytest.fixture
def admin(client, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "ADMIN_SECRET_KEY", KEY)
    client.headers["X-Admin-Key"] = KEY
    return client


def test_endpoints_need_auth(client):
    for path in ("/admin/calls", "/admin/calls/x", "/admin/messages", "/admin/calls/live"):
        assert client.get(path).status_code == 401


async def test_call_history_and_detail(admin, db):
    now = datetime.now(timezone.utc)
    await db.calls.insert_many([
        {"call_id": "call-1", "status": "completed", "outcome": "booked", "started_at": now - timedelta(hours=1),
         "transcript": [{"role": "user", "text": "hi"}], "turns": [{"latency_s": 0.8}]},
        {"call_id": "call-2", "status": "completed", "outcome": "message", "started_at": now,
         "transcript": [], "turns": []},
    ])
    listed = admin.get("/admin/calls").json()["calls"]
    assert [c["call_id"] for c in listed] == ["call-2", "call-1"]
    assert "transcript" not in listed[0]                                  # list stays light
    assert [c["call_id"] for c in admin.get("/admin/calls?outcome=booked").json()["calls"]] == ["call-1"]
    assert admin.get("/admin/calls/call-1").json()["transcript"][0]["text"] == "hi"
    assert admin.get("/admin/calls/missing").status_code == 404


async def test_live_calls_include_voice_agent_and_skip_stale(admin, db):
    now = datetime.now(timezone.utc)
    await db.calls.insert_many([
        {"call_id": "call-live", "status": "in_progress", "channel": "phone", "caller_phone": "+15550001111",
         "started_at": now, "updated_at": now},
        {"call_id": "call-crashed", "status": "in_progress", "started_at": now - timedelta(hours=2),
         "updated_at": now - timedelta(hours=2)},
    ])
    calls = admin.get("/admin/calls/live").json()["calls"]
    livekit = [c for c in calls if c["source"] == "livekit"]
    assert [c["call_sid"] for c in livekit] == ["call-live"]


async def test_messages_list_and_mark_handled(admin, db):
    await db.messages.insert_one({"message_id": "MSG1", "caller_name": "Rahim", "callback_number": "+1555",
                                  "message": "Call me", "urgent": False, "channel": "voice", "status": "new",
                                  "created_at": datetime.now(timezone.utc)})
    body = admin.get("/admin/messages").json()
    assert body["new"] == 1 and body["messages"][0]["message_id"] == "MSG1"
    assert admin.patch("/admin/messages/MSG1", json={"status": "handled"}).json()["status"] == "handled"
    assert admin.get("/admin/messages?status=new").json()["messages"] == []
    assert admin.patch("/admin/messages/MSG1", json={"status": "deleted"}).status_code == 422
    assert admin.patch("/admin/messages/NOPE", json={"status": "handled"}).status_code == 404
