"""Health checks (0.14) and production-safe app defaults (0.4)."""


def test_livez(client):
    r = client.get("/livez")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_readyz_ok_with_database(client):
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["checks"]["database"] == "ok"


def test_readyz_503_when_database_down(client, monkeypatch):
    import database.mongodb as mongodb

    monkeypatch.setattr(mongodb, "_db", None)  # get_db() raises
    r = client.get("/readyz")
    assert r.status_code == 503 and r.json()["status"] == "degraded"
    assert client.get("/health").status_code == 503


def test_health_endpoints_leak_nothing(client):
    for path in ("/", "/livez", "/readyz"):
        body = client.get(path).text.lower()
        for word in ("version", "hotel", "openai", "env", "gpt"):
            assert word not in body, (path, word)


def test_request_id_header(client):
    r = client.get("/livez", headers={"X-Request-ID": "abc123"})
    assert r.headers["X-Request-ID"] == "abc123"


def test_admin_routes_need_auth(client):
    assert client.get("/admin/stats").status_code == 401
    assert client.get("/bookings/").status_code == 401


def test_drain_turns_readiness_off_and_only_from_inside(client, monkeypatch):
    # Audit SRE-11. The test client's address is "testclient", not loopback: refused.
    import main

    assert client.post("/internal/drain").status_code == 404
    monkeypatch.setattr(main, "_draining", True)
    r = client.get("/readyz")
    assert r.status_code == 503 and r.json()["status"] == "draining"
