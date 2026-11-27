"""
One-step onboarding: the website import (crawl → structured data → LLM draft → normalise → apply)
and automatic numbers from the platform (owned or bought, end to end, ignoring plan limits).
"""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from config import settings
from core.auth import service as auth_service
from core.repositories import Repos
from core.services import numbers, site_import
from core.services.tenant_admin import create_tenant
from core.tenancy import invalidate

PASSWORD = "correct horse battery"

HOME = """<html><head><meta property="og:site_name" content="Bella (og)">
<script type="application/ld+json">{"@context": "https://schema.org", "@graph": [{"@type": "HairSalon",
 "name": "Bella Salon", "telephone": "(512) 474-2100",
 "address": {"@type": "PostalAddress", "streetAddress": "12 Main St", "addressLocality": "Austin",
             "addressRegion": "TX", "postalCode": "78701", "addressCountry": "US"},
 "openingHoursSpecification": [{"dayOfWeek": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
                                "opens": "09:00", "closes": "17:00"}]}]}</script></head>
<body><nav><a href="/services">Services &amp; prices</a> <a href="/contact">Contact</a>
<a href="/about">About</a> <a href="https://other.example/x">Partner</a> <a href="/logo.png">logo</a>
<a href="https://book.fresha.example/bella">Book now</a></nav>
<p>Bella Salon is a friendly hair salon in downtown Austin offering cuts, colour and styling.</p></body></html>"""
SERVICES = "<html><body><h1>Prices</h1><p>Haircut $45 (45 min). Colour from $120.</p></body></html>"
CONTACT = '<html><body><a href="mailto:hello@bella.example">Email us</a> Parking behind the shop.</body></html>'


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    auth_service.reset_rate_limits()
    invalidate()
    yield
    invalidate()


def _site_client(requested):
    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        path = request.url.path
        if request.url.host != "bella.example":
            return httpx.Response(200, text="<html>elsewhere</html>", headers={"content-type": "text/html"})
        if path == "/":
            return httpx.Response(200, text=HOME, headers={"content-type": "text/html; charset=utf-8"})
        if path == "/services":
            return httpx.Response(200, text=SERVICES, headers={"content-type": "text/html"})
        if path == "/contact":
            return httpx.Response(200, text=CONTACT, headers={"content-type": "text/html"})
        if path == "/about":                       # a redirect into the private network must be refused
            return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


@pytest.fixture
def public_hosts(monkeypatch):
    monkeypatch.setattr(site_import, "_host_is_public", lambda host: host != "127.0.0.1")


def test_private_addresses_are_refused():
    for url in ("http://127.0.0.1/", "http://10.0.0.5", "ftp://example.com"):
        with pytest.raises(site_import.SiteImportError):
            site_import.public_url(url)


async def test_crawl_stays_on_site_and_structured_data_beats_the_llm(public_hosts, monkeypatch):
    seen_text = {}

    async def fake_extract(crawled, on_progress=None):
        seen_text["text"] = " ".join(p["text"] for p in crawled["pages"])
        return {"name": "Wrong Name", "phone": "+15550000000", "vertical": "salon", "timezone": "Mars/Phobos",
                "description": "A friendly hair salon in Austin.",
                "hours": [{"dow": 5, "open": "25:00", "close": "26:00"}],
                "services": [{"name": "Haircut", "duration_min": 45, "price_display": "$45", "price_amount": 45},
                             {"name": "haircut", "duration_min": 30},          # duplicate name: dropped
                             {"name": "Colour", "price_display": "from $120", "price_amount": 120}],
                "faqs": [{"question": "Is there parking?", "answer": "Yes, behind the shop."}, {"question": "x"}],
                "links": {"menu": "not a url but kept with https"}}, []
    monkeypatch.setattr(site_import, "extract", fake_extract)
    requested: list = []
    async with _site_client(requested) as client:
        draft = await site_import.import_site("bella.example", client=client)

    assert not any("127.0.0.1" in u for u in requested)                  # redirect target never fetched
    assert not any("other.example" in u or "logo.png" in u for u in requested)
    assert set(draft["sources"]) == {"https://bella.example/", "https://bella.example/services",
                                     "https://bella.example/contact"}
    assert "Haircut $45" in seen_text["text"]
    p = draft["profile"]
    assert p["name"] == "Bella Salon" and p["phone"] == "+15124742100"   # JSON-LD wins
    assert p["country"] == "US" and p["currency"] == "USD" and p["vertical"] == "salon"
    assert p["address"]["city"] == "Austin" and p["email"] == "hello@bella.example"
    assert p["timezone"] == "" and any("timezone" in w for w in draft["warnings"])
    assert [h["dow"] for h in draft["hours"]] == [0, 1, 2, 3, 4]           # from JSON-LD, not the bad LLM row
    assert [s["name"] for s in draft["services"]] == ["Haircut", "Colour"]
    assert draft["services"][0]["price"] == {"amount": 4500, "currency": "USD", "display": "$45"}
    assert draft["faqs"] == [{"question": "Is there parking?", "answer": "Yes, behind the shop."}]
    assert draft["links"]["booking"] == "https://book.fresha.example/bella"


def test_normalize_falls_back_to_country_timezone_and_drops_bad_hours():
    draft = site_import.normalize({"country": "United Kingdom", "phone": "020 7946 0018",
                                   "hours": [{"dow": 0, "open": "9:00", "close": "17:00"}, {"dow": 9}]},
                                  {}, "https://x.example", [])
    p = draft["profile"]
    assert p["country"] == "GB" and p["timezone"] == "Europe/London" and p["currency"] == "GBP"
    assert p["phone"] == "+442079460018"
    assert draft["hours"] == [] or all(0 <= h["dow"] <= 6 for h in draft["hours"])
    assert any("opening-hours" in w for w in draft["warnings"])


# ── API ──────────────────────────────────────────────────────────────────

async def _login(client, email):
    r = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.content = status, body, b"x"

    def json(self):
        return self._body


@pytest.fixture
def twilio(monkeypatch):
    """Provisioning on, against a stubbed Twilio / LiveKit; orders run inline instead of on the queue."""
    monkeypatch.setattr(settings, "NUMBER_PROVISIONING_ENABLED", True)
    monkeypatch.setattr(settings, "TELEPHONY_PROVIDER", "twilio")
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setattr(settings, "TWILIO_SIP_TRUNK_SID", "TK1")
    monkeypatch.setattr(settings, "LIVEKIT_SIP_INBOUND_TRUNK_ID", "ST_1")
    monkeypatch.setattr(settings, "NUMBER_VERIFY_CALL", False)
    state = {"owned": {"+442071234567": "PN_GB", "+17405600905": "PN_740", "+15125550001": "PN_TAKEN"},
             "bought": [], "trunk": [], "livekit": []}

    async def find_owned(number):
        return state["owned"].get(number)

    async def twilio_call(method, url, **kw):
        if "IncomingPhoneNumbers.json" in url and method == "GET":
            return _Resp(200, {"incoming_phone_numbers": [
                {"phone_number": n, "sid": sid, "friendly_name": "x", "capabilities": {"voice": True}}
                for n, sid in state["owned"].items()], "next_page_uri": None})
        if "IncomingPhoneNumbers.json" in url and method == "POST":
            state["bought"].append(kw["data"]["PhoneNumber"])
            return _Resp(201, {"sid": "PN_NEW"})
        raise AssertionError(f"unexpected Twilio call {method} {url}")

    async def attach(sid):
        state["trunk"].append(sid)

    async def lk(add=None, remove=None):
        state["livekit"] += add or []

    async def inline(order_id):
        from database.mongodb import get_db
        await numbers.run_order(get_db(), order_id)
    monkeypatch.setattr(numbers, "_find_owned", find_owned)
    monkeypatch.setattr(numbers, "_twilio", twilio_call)
    monkeypatch.setattr(numbers, "_attach_trunk", attach)
    monkeypatch.setattr(numbers, "_livekit_numbers", lk)
    monkeypatch.setattr(numbers, "run_order_in_background", inline)
    return state


@pytest.fixture
def mailbox(monkeypatch):
    sent = []

    async def fake_send(to, subject, text, html=None):
        sent.append({"to": to, "subject": subject, "text": text})
        return True
    import routers.platform as platform_router
    monkeypatch.setattr(platform_router, "send_email", fake_send)
    return sent


@pytest.fixture
async def root(db, client):
    await auth_service.create_user(email="root@platform.io", password=PASSWORD, platform_admin=True)
    return _h(await _login(client, "root@platform.io"))


async def test_one_step_provisioning_from_a_draft_with_an_owned_number(db, client, root, twilio, mailbox):
    r = client.post("/platform/tenants", headers=root, json={
        "name": "Bella Salon", "vertical": "salon", "timezone": "Europe/London", "country": "GB", "currency": "GBP",
        "owner_email": "ann@bella.example", "owner_name": "Ann",
        "description": "A friendly hair salon.", "website": "https://bella.example",
        "address": {"line1": "12 High St", "city": "London"},
        "hours": [{"dow": 1, "open": "10:00", "close": "18:00"}],
        "services": [{"name": "Cut & finish", "duration_min": 45, "price": {"amount": 4500, "display": "£45"}}],
        "faqs": [{"question": "Parking?", "answer": "On the street."}],
        "links": {"booking": "https://book.example/bella"},
        "number": {"source": "owned", "e164": "+44 20 7123 4567"}})       # GB, but owned: no documents wait
    assert r.status_code == 200, r.text
    body = r.json()
    tid = body["tenant"]["id"]
    assert body["emailed"] is True and body["number_error"] is None and body["added"] == {"services": 1, "knowledge": 1}

    tenant = await db.tenants.find_one({"_id": tid})
    assert tenant["description"] == "A friendly hair salon." and tenant["address"]["city"] == "London"
    assert tenant["hours"] == [{"dow": 1, "open": "10:00", "close": "18:00"}]
    assert tenant["onboarding"]["by"] == "platform" and tenant["onboarding"]["completed_at"]
    repos = Repos(db, tid)
    assert [s["name"] for s in await repos.services.find()] == ["Cut & finish"]          # examples replaced
    assert {k["source"] for k in await repos.knowledge.find()} == {"website"}

    row = await db.phone_numbers.find_one({"e164": "+442071234567"})
    assert row["tenant_id"] == tid and row["status"] == "active" and row["provider_sid"] == "PN_GB"
    assert twilio["bought"] == [] and twilio["trunk"] == ["PN_GB"] and twilio["livekit"] == ["+442071234567"]

    assert mailbox[0]["to"] == "ann@bella.example" and "reset-password?token=" in mailbox[0]["text"]
    reset = await db.password_resets.find_one({})
    expires = reset["expires_at"] if reset["expires_at"].tzinfo else reset["expires_at"].replace(tzinfo=timezone.utc)
    assert expires - datetime.now(timezone.utc) > timedelta(days=6)                    # welcome link lasts a week
    details = client.get(f"/platform/tenants/{tid}", headers=root).json()
    assert details["number_orders"][0]["status"] == "active"


async def test_a_bad_field_creates_nothing(db, client, root, mailbox):
    r = client.post("/platform/tenants", headers=root, json={
        "name": "Broken", "owner_email": "x@y.example", "hours": [{"dow": 9, "open": "x", "close": "y"}]})
    assert r.status_code == 422
    assert await db.tenants.count_documents({"name": "Broken"}) == 0 and mailbox == []


async def test_owned_list_hides_assigned_numbers_and_platform_ignores_plan_limits(db, client, root, twilio):
    t = await create_tenant(name="Full Plan", vertical="generic", tenant_id="ten_F")
    await db.tenants.update_one({"_id": t.id}, {"$set": {"limits.max_numbers": 1}})
    await db.phone_numbers.insert_one({"e164": "+15125550001", "tenant_id": t.id, "status": "active", "provider": "twilio"})
    invalidate()

    owned = client.get("/platform/numbers/owned", headers=root).json()
    assert owned["mode"] == "twilio" and {n["e164"] for n in owned["numbers"]} == {"+442071234567", "+17405600905"}

    r = client.post(f"/platform/tenants/{t.id}/numbers", headers=root, json={"e164": "+15125550199", "source": "buy"})
    assert r.status_code == 200, r.text                                        # over max_numbers: allowed here
    assert twilio["bought"] == ["+15125550199"]
    assert (await db.phone_numbers.find_one({"e164": "+15125550199"}))["status"] == "active"
    taken = client.post(f"/platform/tenants/{t.id}/numbers", headers=root, json={"e164": "+15125550001", "source": "owned"})
    assert taken.status_code == 200                                            # already this business's: reconnects
    missing = client.post(f"/platform/tenants/{t.id}/numbers", headers=root, json={"e164": "+15125559999", "source": "owned"})
    assert missing.status_code == 404


async def test_numbers_off_says_what_to_set(client, root, monkeypatch):
    monkeypatch.setattr(settings, "NUMBER_PROVISIONING_ENABLED", False)
    off = client.get("/platform/numbers/owned", headers=root).json()
    assert off["mode"] == "off" and any("NUMBER_PROVISIONING_ENABLED" in p for p in off["problems"])
    r = client.post("/platform/tenants", headers=root, json={
        "name": "No Numbers", "owner_email": "n@n.example", "number": {"source": "buy", "e164": "+15125550111"}})
    assert r.status_code == 503


async def test_the_import_reports_what_it_reads_as_it_goes(public_hosts, monkeypatch):
    async def fake_extract(crawled, on_progress=None):
        return {"services": [{"name": "Haircut", "price_display": "$45"}]}, []
    monkeypatch.setattr(site_import, "extract", fake_extract)
    events: list = []

    async def listen(e):
        events.append(e)
    async with _site_client([]) as client:
        draft = await site_import.import_site("bella.example", client=client, on_progress=listen)

    kinds = [e["type"] for e in events]
    assert kinds[0] == "step" and events[0]["id"] == "home" and events[0]["status"] == "running"
    pages = [e for e in events if e["type"] == "page"]
    assert {e["url"] for e in pages if e["ok"]} == set(draft["sources"])       # one event per page read
    assert any(not e["ok"] for e in pages)                                     # the refused redirect is shown
    found = {e["field"]: e for e in events if e["type"] == "found"}
    assert found["name"]["value"] == "Bella Salon" and found["services"]["value"] == "1 found"
    assert found["hours"]["ok"] is True and kinds.index("found") > kinds.index("page")


async def test_a_listener_that_breaks_never_stops_the_import(public_hosts, monkeypatch):
    async def fake_extract(crawled, on_progress=None):
        return {}, []
    monkeypatch.setattr(site_import, "extract", fake_extract)

    async def broken(_e):
        raise RuntimeError("tab closed")
    async with _site_client([]) as client:
        draft = await site_import.import_site("bella.example", client=client, on_progress=broken)
    assert draft["profile"]["name"] == "Bella Salon"


def test_the_llm_is_told_to_write_in_english():
    prompt = site_import._prompt({"pages": [{"url": "https://x.example/", "text": "Hola"}], "hints": {}},
                                 [{"key": "dental", "label": "Dental", "description": "d"}])
    assert "in clear English, translating" in prompt and "exactly as written" in prompt


async def test_streamed_site_import_sends_progress_then_the_draft(db, client, monkeypatch):
    a = await create_tenant(name="Salon A", vertical="salon", tenant_id="ten_A")
    await auth_service.create_user(email="owner@a.com", password=PASSWORD, tenant_id=a.id, role="owner")
    owner = _h(await _login(client, "owner@a.com"))

    async def fake_import(url, client=None, on_progress=None):
        await on_progress({"type": "step", "id": "home", "status": "running", "label": "Opening a.example…"})
        await on_progress({"type": "page", "url": "https://a.example/", "title": "A", "words": 120, "ok": True})
        return {"profile": {"name": "Salon A"}, "hours": [], "services": [], "faqs": [], "links": {},
                "sources": [url], "warnings": []}
    monkeypatch.setattr(site_import, "import_site", fake_import)
    monkeypatch.setattr(site_import, "_host_is_public", lambda host: True)
    r = client.post("/t/onboarding/site-import/stream", headers=owner, json={"url": "a.example"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["x-accel-buffering"] == "no"
    frames = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    assert [f["type"] for f in frames] == ["step", "page", "result"]
    assert frames[-1]["draft"]["profile"]["name"] == "Salon A"

    async def failing(url, client=None, on_progress=None):
        raise site_import.SiteImportError("Couldn't load that website", status=502)
    monkeypatch.setattr(site_import, "import_site", failing)
    r = client.post("/t/onboarding/site-import/stream", headers=owner, json={"url": "a.example"})
    last = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")][-1]
    assert last == {"type": "error", "status": 502, "message": "Couldn't load that website"}


async def test_owner_site_import_and_apply_stay_in_their_business(db, client, monkeypatch):
    a = await create_tenant(name="Salon A", vertical="salon", tenant_id="ten_A")
    b = await create_tenant(name="Salon B", vertical="salon", tenant_id="ten_B")
    await Repos(db, a.id).services.insert_one({"_id": "svc_own", "name": "Owner's own service", "duration_min": 30,
                                               "active": True, "source": "owner"})
    await auth_service.create_user(email="owner@a.com", password=PASSWORD, tenant_id=a.id, role="owner")
    await auth_service.create_user(email="staff@a.com", password=PASSWORD, tenant_id=a.id, role="staff")
    owner, staff = _h(await _login(client, "owner@a.com")), _h(await _login(client, "staff@a.com"))

    async def fake_import(url, client=None, on_progress=None):
        return {"profile": {"name": "Salon A"}, "hours": [], "services": [], "faqs": [], "links": {},
                "sources": [url], "warnings": []}
    monkeypatch.setattr(site_import, "import_site", fake_import)
    monkeypatch.setattr(site_import, "_host_is_public", lambda host: True)
    assert client.post("/t/onboarding/site-import", headers=staff, json={"url": "a.example"}).status_code == 403
    r = client.post("/t/onboarding/site-import", headers=owner, json={"url": "a.example"})
    assert r.status_code == 200 and r.json()["draft"]["profile"]["name"] == "Salon A"

    applied = client.post("/t/onboarding/apply-import", headers=owner, json={
        "profile": {"description": "Cuts and colour.", "phone": "+15124742100"},
        "services": [{"name": "Website cut", "duration_min": 40}],
        "faqs": [{"question": "Cards?", "answer": "Yes."}]})
    assert applied.status_code == 200, applied.text
    names = sorted(s["name"] for s in await Repos(db, a.id).services.find())
    assert names == ["Owner's own service", "Website cut"]                    # examples gone, owner's row kept
    assert (await db.tenants.find_one({"_id": a.id}))["description"] == "Cuts and colour."
    assert len(await Repos(db, b.id).services.find()) == 4                    # B untouched (salon examples)
    assert await db.audit_log.find_one({"tenant_id": a.id, "action": "site_import.apply"})
    assert json.dumps(applied.json())


# ── Audit P0-02 / SEC-12: SSRF hardening ─────────────────────────────────────

def test_only_web_ports_are_fetched(monkeypatch):
    from core.services import site_import as si

    monkeypatch.setattr(si, "_host_is_public", lambda h: True)
    assert si.public_url("https://example.com:443/x")
    for bad in ("http://example.com:6379/", "https://example.com:27017", "http://example.com:99999/"):
        with pytest.raises(si.SiteImportError):
            si.public_url(bad)


async def test_a_rebound_address_is_refused_after_connecting(monkeypatch):
    import httpx

    from core.services import site_import as si

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"<html>secret metadata</html>"

    class FakeNet:
        def get_extra_info(self, name):
            return ("169.254.169.254", 80) if name == "server_addr" else None

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, stream=Stream(),
                              extensions={"network_stream": FakeNet()})

    monkeypatch.setattr(si, "_host_is_public", lambda h: True)          # the first DNS answer looked public
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(si.SiteImportError, match="public website"):
            await si.fetch(client, "http://rebind.example/")
    assert not si._ip_is_public("::ffff:10.0.0.1") and si._ip_is_public("1.1.1.1")


def test_every_dns_answer_must_be_public(monkeypatch):
    import socket

    from core.services import site_import as si

    def answers(*ips):
        return lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

    monkeypatch.setattr(socket, "getaddrinfo", answers("93.184.216.34", "10.0.0.5"))
    with pytest.raises(si.SiteImportError, match="public website"):
        si.resolve_public("mixed.example", 443)
    monkeypatch.setattr(socket, "getaddrinfo", answers("93.184.216.34"))
    assert si.resolve_public("ok.example", 443) == "93.184.216.34"


async def test_the_connection_goes_to_the_address_that_was_checked(monkeypatch):
    import socket

    import httpx

    from core.services import site_import as si

    lookups = iter(["93.184.216.34", "127.0.0.1"])     # a rebinding DNS: public first, then internal
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(lookups), port))])
    dialled = []

    async def fake_connect(self, host, port, **kw):
        dialled.append((host, port))
        raise httpx.ConnectError("stop here")          # never touch the network in tests

    monkeypatch.setattr(si.httpcore.AnyIOBackend, "connect_tcp", fake_connect)
    async with httpx.AsyncClient(transport=si.pinned_transport()) as client:
        with pytest.raises(Exception):
            await client.get("https://rebind.example/")
    assert dialled == [("93.184.216.34", 443)]          # one lookup, and that exact address was dialled


async def test_a_name_that_resolves_inside_is_never_dialled(monkeypatch):
    import socket

    import httpx

    from core.services import site_import as si

    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))])

    async def no_connect(self, host, port, **kw):
        raise AssertionError("an internal address must not be dialled")

    monkeypatch.setattr(si.httpcore.AnyIOBackend, "connect_tcp", no_connect)
    async with httpx.AsyncClient(transport=si.pinned_transport()) as client:
        with pytest.raises(si.SiteImportError, match="public website"):
            await client.get("http://metadata.example/")
