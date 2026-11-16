"""Permanent /talk links: never expire, new room per visit, rate limited, revocable; the public app is minimal."""

import pytest
from fastapi.testclient import TestClient
from livekit import api

from config import settings
from core.ratelimit import SlidingWindow, public_calls
from core.signed_links import build_permanent_link, permanent_key, verify_permanent


@pytest.fixture(autouse=True)
def _fresh_limits():
    public_calls.reset()
    yield
    public_calls.reset()


def _token(client, key=None, ref="default"):
    key = permanent_key(ref) if key is None else key
    return client.get(f"/livekit/token?perm={key}&t={ref}")


def _claims(token: str):
    return api.TokenVerifier(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET).verify(token)


def test_key_is_stable_and_per_business():
    assert permanent_key("default") == permanent_key("default")
    assert permanent_key("default") != permanent_key("ten_other")
    assert verify_permanent("default", permanent_key("default"))
    assert not verify_permanent("default", permanent_key("ten_other"))
    assert not verify_permanent("default", "") and not verify_permanent("default", None)


def test_built_link_shape():
    link = build_permanent_link("https://x.example.com/", "default")
    assert link == f"https://x.example.com/talk/default?k={permanent_key('default')}"


def test_valid_key_gets_a_token_for_a_fresh_room_each_visit(client):
    a, b = _token(client), _token(client)
    assert a.status_code == b.status_code == 200
    assert a.json()["room"] != b.json()["room"] and a.json()["room"].startswith("web-")
    claims = _claims(a.json()["token"])
    assert [x.agent_name for x in claims.room_config.agents] == [settings.LIVEKIT_AGENT_NAME]
    assert claims.video.room == a.json()["room"]


def test_wrong_or_missing_key_is_403(client):
    assert _token(client, key="0" * 32).status_code == 403
    assert _token(client, key=permanent_key("ten_other")).status_code == 403   # another business's key
    assert client.get("/livekit/token").status_code == 422                     # no room, no key


def test_raising_the_version_turns_old_links_off(client, monkeypatch):
    old = permanent_key("default")
    assert _token(client, key=old).status_code == 200
    monkeypatch.setattr(settings, "CALL_LINK_VERSION", settings.CALL_LINK_VERSION + 1)
    assert _token(client, key=old).status_code == 403
    assert _token(client).status_code == 200            # the new key works


def test_one_visitor_is_limited_per_hour(client, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_CALL_RATE_PER_IP_HOUR", 2)
    assert [_token(client).status_code for _ in range(3)] == [200, 200, 429]


def test_everyone_together_is_limited_per_day(client, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_CALL_MAX_PER_DAY", 2)
    codes = [client.get(f"/livekit/token?perm={permanent_key('default')}&t=default",
                        headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code for i in range(3)]
    assert codes == [200, 200, 429]                      # different visitors, same daily cap


def test_forged_keys_do_not_use_up_the_budget(client, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_CALL_RATE_PER_IP_HOUR", 1)
    for _ in range(5):
        assert _token(client, key="f" * 32).status_code == 403
    assert _token(client).status_code == 200


def test_talk_page_is_served(client):
    r = client.get("/talk/default")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]


def test_the_expiring_signed_link_still_works(client):
    from core.signed_links import sign_call_room
    exp, sig = sign_call_room("wa_0123456789abcdef")
    assert client.get(f"/livekit/token?room=wa_0123456789abcdef&exp={exp}&sig={sig}").status_code == 200


def test_sliding_window_forgets_old_hits():
    w = SlidingWindow()
    assert [w.allow("k", 2, 10, now=t) for t in (0, 1, 2)] == [True, True, False]
    assert w.allow("k", 2, 10, now=11.5)                 # the hit at t=0 has aged out


def test_public_app_exposes_only_the_call_routes():
    import public_call

    c = TestClient(public_call.app)
    assert c.get("/health").status_code == 200
    assert c.get("/talk/default").status_code == 200
    for path in ("/docs", "/openapi.json", "/admin/login", "/auth/login", "/platform/tenants", "/t/context", "/voice/incoming"):
        assert c.get(path).status_code in (404, 405), path
    assert _token(c).status_code == 200
