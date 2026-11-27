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


# ── The call page is branded for the link's business ─────────────────────────────────────────

def _dental(name="Bilby Dental"):
    from core.tenancy.models import AgentConfig, TenantConfig
    return TenantConfig(_id="ten_dental1", name=name, slug="bilby", vertical="dental", locale="en-AU",
                        agent=AgentConfig(name="Aria"))


def test_call_page_shows_the_business_not_a_hotel(client, monkeypatch):
    import routers.livekit as lk

    async def fake(tid):
        return _dental() if tid == "ten_dental1" else None
    monkeypatch.setattr(lk, "resolve_by_id", fake)
    exp, sig = sign_call_room("test_abc", tenant_id="ten_dental1")
    page = client.get(f"/call/test_abc?exp={exp}&sig={sig}&t=ten_dental1").text
    assert "Bilby Dental" in page and "Aria" in page and 'lang="en-AU"' in page and "Powered by <b>LaudLine</b>" in page
    assert "Hotel" not in page and "🏨" not in page and "__" + "NAME__" not in page
    assert '"problem": ""' in page


def test_business_name_cannot_break_out_of_the_page(client, monkeypatch):
    import routers.livekit as lk

    async def fake(_tid):
        return _dental('Evil</script><script>alert(1)</script>')
    monkeypatch.setattr(lk, "resolve_by_id", fake)
    exp, sig = sign_call_room("test_abc", tenant_id="ten_dental1")
    page = client.get(f"/call/test_abc?exp={exp}&sig={sig}&t=ten_dental1").text
    assert "<script>alert(1)" not in page


def test_expired_link_page_says_so_and_names_no_business(client, monkeypatch):
    import routers.livekit as lk

    async def fake(_tid):
        raise AssertionError("a bad link must not look up the business")
    monkeypatch.setattr(lk, "resolve_by_id", fake)
    exp, sig = sign_call_room("test_abc", now=time.time() - 3600, tenant_id="ten_dental1")
    page = client.get(f"/call/test_abc?exp={exp}&sig={sig}&t=ten_dental1").text
    assert "invalid or has expired" in page and "Bilby" not in page and "Hotel" not in page


def test_call_page_sends_the_business_id_with_the_token_request():
    """Dashboard test links are signed for a business (?t=…); without it the token check always failed."""
    from routers.livekit import CALL_HTML_PATH
    assert 'qs.set("t", link.get("t"))' in CALL_HTML_PATH.read_text(encoding="utf-8")


def test_call_page_opens_the_microphone_before_joining_and_never_leaves_a_room_behind():
    """"Could not start audio source" (NotReadableError): the mic is opened first, with fallbacks, so a broken
    microphone never sends the receptionist into an empty room; a failed join leaves the room."""
    from routers.livekit import CALL_HTML_PATH
    page = CALL_HTML_PATH.read_text(encoding="utf-8")
    assert page.index("acquireMic(micSelect.value") < page.index("r.connect(data.url")
    assert "publishTrack(micTrack" in page and "setMicrophoneEnabled(true)" not in page
    assert '"NotReadableError"' in page and "voiceIsolation: false" in page and "getUserMedia({ audio: true })" in page
    assert "abandonRoom()" in page and "removeAllListeners" in page
    assert "audioCaptureDefaults: BASE" in page   # Room options, not connect() options (where it was ignored)


def test_a_link_signed_for_a_business_gets_a_token_with_its_id(client, monkeypatch):
    import routers.livekit as lk

    async def fake(_tid):
        return _dental()
    monkeypatch.setattr(lk, "resolve_by_id", fake)
    exp, sig = sign_call_room("test_abc", tenant_id="ten_dental1")
    assert client.get(f"/livekit/token?room=test_abc&exp={exp}&sig={sig}").status_code == 403
    r = client.get(f"/livekit/token?room=test_abc&exp={exp}&sig={sig}&t=ten_dental1")
    assert r.status_code == 200 and r.json()["business_name"] == "Bilby Dental"
