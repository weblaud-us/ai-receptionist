"""
Account settings: the signed-in user changes their own name, password and e-mail, and sees and
ends their sessions. The bootstrap .env admin has no stored account, so it is refused.
"""

import pytest

from core.auth import service as auth_service
from core.integrations import email as email_mod

PASSWORD = "correct horse battery"
NEW_PASSWORD = "a brand new secret"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "COOKIE_SECURE", False)   # TestClient talks plain http
    auth_service.reset_rate_limits()
    yield


@pytest.fixture
def mails(monkeypatch):
    sent = []

    async def fake(to, subject, text, html=None):
        sent.append({"to": to, "subject": subject, "text": text})
        return True

    monkeypatch.setattr(email_mod, "send_email", fake)
    import routers.auth as auth_router

    monkeypatch.setattr(auth_router, "send_email", fake)
    return sent


def _login(client, email, password=PASSWORD):
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _token_from(mail):
    return mail["text"].split("token=")[1].split()[0]


async def test_change_name_shows_after_refresh(db, client):
    await auth_service.create_user(email="u@x.com", password=PASSWORD, name="Old")
    h = _login(client, "u@x.com")
    r = client.patch("/auth/profile", json={"name": "  New Name  "}, headers=h)
    assert r.status_code == 200 and r.json()["user"]["name"] == "New Name"
    assert client.post("/auth/refresh").json()["user"]["name"] == "New Name"


async def test_change_password_needs_current_and_signs_out_other_sessions(db, client, mails):
    await auth_service.create_user(email="u@x.com", password=PASSWORD)
    other = client.__class__(client.app)                     # a second browser
    other.post("/auth/login", json={"email": "u@x.com", "password": PASSWORD})
    h = _login(client, "u@x.com")

    bad = client.post("/auth/password/change", json={"current_password": "wrong-wrong", "new_password": NEW_PASSWORD},
                      headers=h)
    assert bad.status_code == 400                             # never 401: that would log the dashboard out
    weak = client.post("/auth/password/change", json={"current_password": PASSWORD, "new_password": "short"},
                       headers=h)
    assert weak.status_code == 400
    same = client.post("/auth/password/change", json={"current_password": PASSWORD, "new_password": PASSWORD},
                       headers=h)
    assert same.status_code == 400

    ok = client.post("/auth/password/change", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
                     headers=h)
    assert ok.status_code == 200 and ok.json()["signed_out"] == 1
    assert client.post("/auth/refresh").status_code == 200    # this browser stays signed in
    assert other.post("/auth/refresh").status_code == 401     # the other one is out
    assert mails[-1]["to"] == "u@x.com" and "password was changed" in mails[-1]["subject"]
    assert client.post("/auth/login", json={"email": "u@x.com", "password": PASSWORD}).status_code == 401
    _login(client, "u@x.com", NEW_PASSWORD)


async def test_wrong_current_password_is_rate_limited(db, client):
    await auth_service.create_user(email="u@x.com", password=PASSWORD)
    h = _login(client, "u@x.com")
    body = {"current_password": "wrong-wrong", "new_password": NEW_PASSWORD}
    for _ in range(5):
        assert client.post("/auth/password/change", json=body, headers=h).status_code == 400
    body["current_password"] = PASSWORD
    assert client.post("/auth/password/change", json=body, headers=h).status_code == 429


async def test_change_email_is_confirmed_from_the_new_address(db, client, mails):
    await auth_service.create_user(email="old@x.com", password=PASSWORD)
    await auth_service.create_user(email="taken@x.com", password=PASSWORD)
    h = _login(client, "old@x.com")

    assert client.post("/auth/email/change", json={"password": "wrong-wrong", "new_email": "new@x.com"},
                       headers=h).status_code == 400
    assert client.post("/auth/email/change", json={"password": PASSWORD, "new_email": "taken@x.com"},
                       headers=h).status_code == 409
    assert client.post("/auth/email/change", json={"password": PASSWORD, "new_email": "not-an-email"},
                       headers=h).status_code == 400

    r = client.post("/auth/email/change", json={"password": PASSWORD, "new_email": " New@X.com "}, headers=h)
    assert r.status_code == 200 and r.json()["pending_email"] == "new@x.com" and r.json()["emailed"]
    assert mails[-1]["to"] == "new@x.com"
    assert client.get("/auth/account", headers=h).json()["pending_email"] == "new@x.com"
    # Nothing changes until the link is opened.
    assert client.get("/auth/me", headers=h).json()["user"]["email"] == "old@x.com"

    token = _token_from(mails[-1])
    done = client.post("/auth/email/confirm", json={"token": token})
    assert done.status_code == 200 and done.json()["email"] == "new@x.com"
    assert mails[-1]["to"] == "old@x.com"                     # the old address is told
    assert client.post("/auth/email/confirm", json={"token": token}).status_code == 400   # one use
    _login(client, "new@x.com")
    assert client.post("/auth/login", json={"email": "old@x.com", "password": PASSWORD}).status_code == 401


async def test_email_taken_after_the_request_is_refused_at_confirm(db, client, mails):
    await auth_service.create_user(email="old@x.com", password=PASSWORD)
    h = _login(client, "old@x.com")
    client.post("/auth/email/change", json={"password": PASSWORD, "new_email": "new@x.com"}, headers=h)
    await auth_service.create_user(email="new@x.com", password=PASSWORD)
    assert client.post("/auth/email/confirm", json={"token": _token_from(mails[-1])}).status_code == 409


async def test_cancel_email_change(db, client, mails):
    await auth_service.create_user(email="old@x.com", password=PASSWORD)
    h = _login(client, "old@x.com")
    client.post("/auth/email/change", json={"password": PASSWORD, "new_email": "new@x.com"}, headers=h)
    assert client.delete("/auth/email/change", headers=h).status_code == 200
    assert client.get("/auth/account", headers=h).json()["pending_email"] is None
    assert client.post("/auth/email/confirm", json={"token": _token_from(mails[-1])}).status_code == 400


async def test_unsent_email_change_is_refused_outside_development(db, client, monkeypatch):
    from config import settings
    import routers.auth as auth_router

    async def fail(*a, **k):
        return False

    monkeypatch.setattr(auth_router, "send_email", fail)
    monkeypatch.setattr(settings, "APP_ENV", "staging")
    await auth_service.create_user(email="old@x.com", password=PASSWORD)
    h = _login(client, "old@x.com")
    r = client.post("/auth/email/change", json={"password": PASSWORD, "new_email": "new@x.com"}, headers=h)
    assert r.status_code == 503
    assert client.get("/auth/account", headers=h).json()["pending_email"] is None


async def test_sessions_list_and_sign_out(db, client):
    await auth_service.create_user(email="u@x.com", password=PASSWORD)
    other = client.__class__(client.app)
    other.post("/auth/login", json={"email": "u@x.com", "password": PASSWORD}, headers={"User-Agent": "Phone/1"})
    h = _login(client, "u@x.com")

    rows = client.get("/auth/sessions", headers=h).json()["sessions"]
    assert len(rows) == 2 and rows[0]["current"] and not rows[1]["current"]
    assert rows[1]["user_agent"] == "Phone/1"

    other.post("/auth/refresh")                               # rotation keeps the device and sign-in time
    rows = client.get("/auth/sessions", headers=h).json()["sessions"]
    phone = next(r for r in rows if not r["current"])
    assert phone["user_agent"] == "Phone/1" and phone["signed_in_at"] == rows[1]["signed_in_at"]

    assert client.delete(f"/auth/sessions/{phone['id']}", headers=h).status_code == 200
    assert other.post("/auth/refresh").status_code == 401
    assert client.delete(f"/auth/sessions/{phone['id']}", headers=h).status_code == 404

    other.post("/auth/login", json={"email": "u@x.com", "password": PASSWORD})
    r = client.post("/auth/sessions/revoke-others", headers=h)
    assert r.status_code == 200 and r.json()["signed_out"] == 1
    assert other.post("/auth/refresh").status_code == 401
    assert client.post("/auth/refresh").status_code == 200


async def test_cannot_end_someone_elses_session(db, client):
    await auth_service.create_user(email="a@x.com", password=PASSWORD)
    await auth_service.create_user(email="b@x.com", password=PASSWORD)
    other = client.__class__(client.app)
    other.post("/auth/login", json={"email": "b@x.com", "password": PASSWORD})
    b_session = (await db.sessions.find_one({}))["_id"]
    h = _login(client, "a@x.com")
    assert client.delete(f"/auth/sessions/{b_session}", headers=h).status_code == 404
    assert other.post("/auth/refresh").status_code == 200


async def test_bootstrap_env_admin_cannot_change_its_account(db, client, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "env-admin-password")
    h = _login(client, "admin", "env-admin-password")
    assert client.get("/auth/account", headers=h).json()["legacy"] is True
    assert client.get("/auth/me", headers=h).json()["user"]["legacy"] is True
    assert client.patch("/auth/profile", json={"name": "x"}, headers=h).status_code == 400
    assert client.post("/auth/password/change", json={"current_password": "a", "new_password": NEW_PASSWORD},
                       headers=h).status_code == 400
    assert client.get("/auth/sessions", headers=h).status_code == 400
