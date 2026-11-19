"""
Phase 7 — pilot, launch and sales (ROADMAP §12). No network, no LLM.

Covered:
- the results report: calls answered, "would have been missed" by answering mode, bookings and their
  value, handled without staff, latency, owner satisfaction;
- the monthly "your assistant this month" e-mail and the weekly pilot report — each sent once per period;
- transcript review: queue (pilots: every call; others: a sample), verdicts, redacted eval cases,
  approve → export;
- margin per business for the platform admin;
- pilot flag set by platform admins only.
"""

from datetime import datetime, timedelta, timezone

import pytest

from config import settings
from core.auth import service as auth_service
from core.repositories import Repos
from core.services import insights, reviews
from core.services.tenant_admin import create_tenant
from core.tenancy import invalidate
from core.tenancy.models import TenantConfig
from services import tenant_jobs

PASSWORD = "correct horse battery"
NOW = datetime(2026, 12, 1, 15, 0, tzinfo=timezone.utc)          # Tuesday 1 December, 09:00 in Chicago


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    auth_service.reset_rate_limits()
    invalidate()
    yield
    invalidate()


def _h(token, tenant=None):
    h = {"Authorization": f"Bearer {token}"}
    if tenant:
        h["X-Tenant-Id"] = tenant
    return h


async def _login(client, email):
    r = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def _tenant(tid="ten_A", **extra) -> TenantConfig:
    extra.setdefault("notifications", {"owner_email": "owner@salon.com"})
    return await create_tenant(name="Bella Salon", vertical="salon", timezone="America/Chicago", tenant_id=tid,
                               status="active", extra=extra, seed_template=False)


async def _calls(db, tid, now, rows):
    repos = Repos(db, tid)
    for i, row in enumerate(rows):
        await repos.calls.insert_one({"call_id": f"{tid}_c{i}", "status": "completed", "channel": "phone",
                                      "direction": "inbound", "started_at": now - timedelta(days=1, minutes=i),
                                      "duration_s": 120, "transcript": [{"role": "user", "text": "Hello"}], **row})


# ── Results report ──────────────────────────────────────────

async def test_value_report_counts_what_sells(db):
    t = await _tenant()
    now = datetime.now(timezone.utc)
    await _calls(db, t.id, now, [
        {"outcome": "booked", "after_hours": True, "latency": {"p50_s": 0.8}},
        {"outcome": "message", "after_hours": True, "latency": {"p50_s": 1.0}},
        {"outcome": "transferred", "transfer": "ok", "after_hours": False, "latency": {"p50_s": 1.2}},
        {"outcome": "faq", "after_hours": False},
        {"outcome": "busy"},                                     # refused at the call limit: not answered
        {"outcome": "completed", "channel": "browser"},           # a test call from the dashboard
        {"outcome": "completed", "direction": "outbound"},        # not inbound
    ])
    repos = Repos(db, t.id)
    await repos.appointments.insert_one({"appointment_id": "a1", "source": "voice", "status": "confirmed",
                                         "price": {"amount": 4500, "currency": "USD"}, "created_at": now - timedelta(days=1)})
    await repos.appointments.insert_one({"appointment_id": "a2", "source": "dashboard", "status": "confirmed",
                                         "price": {"amount": 9900}, "created_at": now - timedelta(days=1)})
    await repos.messages.insert_one({"message_id": "m1", "status": "new", "created_at": now - timedelta(days=1)})
    await db.owner_feedback.insert_one({"tenant_id": t.id, "score": 9, "comment": "Great", "at": now})

    r = await insights.value_report(db, t, now - timedelta(days=7), now)
    assert r["calls_answered"] == 4 and r["minutes"] == 8
    assert r["would_have_missed"] == 2                            # all_calls mode: only the after-hours ones
    assert r["bookings"] == 1 and r["est_booking_value"] == 45.0
    assert r["messages"] == 1 and r["transfers"] == 1 and r["handled_without_human_pct"] == 75.0
    assert r["latency_p50_s"] == 1.0
    assert r["satisfaction"]["score"] == 9
    assert sum(d["calls"] for d in r["by_day"]) == 4

    overflow = t.model_copy(update={"answering_mode": "overflow"})
    assert (await insights.value_report(db, overflow, now - timedelta(days=7), now))["would_have_missed"] == 4


def test_report_text_reads_like_a_person_wrote_it():
    t = TenantConfig(_id="ten_A", name="Bella Salon", slug="bella")
    r = {"calls_answered": 12, "minutes": 30, "would_have_missed": 5, "bookings": 3, "est_booking_value": 135.0,
         "currency": "USD", "leads": 0, "messages": 2, "transfers": 1, "handled_without_human_pct": 91.7,
         "latency_p50_s": 0.9}
    text = insights.report_text(t, r, "Your assistant in November:", unanswered=["Do you do nails?"])
    assert "12 calls answered" in text and "5 of them would otherwise have been missed" in text
    assert "worth about 135 USD" in text and "Do you do nails?" in text and "92% handled" in text


async def test_insights_api_and_feedback(db, client):
    await _tenant()
    await auth_service.create_user(email="staff@a.com", password=PASSWORD, tenant_id="ten_A", role="staff")
    token = await _login(client, "staff@a.com")
    r = client.get("/t/insights", params={"days": 7}, headers=_h(token))
    assert r.status_code == 200 and r.json()["calls_answered"] == 0 and r.json()["days"] == 7
    assert client.post("/t/feedback", json={"score": 11}, headers=_h(token)).status_code == 422
    assert client.post("/t/feedback", json={"score": 8, "comment": "Good week"}, headers=_h(token)).status_code == 200
    assert client.get("/t/insights", headers=_h(token)).json()["satisfaction"]["score"] == 8


async def test_call_records_whether_the_business_was_closed(db):
    from tests.conftest import livekit_agents_importable

    if not livekit_agents_importable():
        pytest.skip("livekit-agents can't be imported on this machine")
    from agent.persistence import call_document
    from agent.profile import VoiceProfile
    from agent.state import CallState

    t = await _tenant(hours=[{"dow": d, "open": "09:00", "close": "17:00"} for d in range(5)])
    sunday = datetime(2026, 11, 22, 18, 0, tzinfo=timezone.utc)
    monday_noon = datetime(2026, 11, 23, 18, 0, tzinfo=timezone.utc)     # 12:00 in Chicago
    closed = CallState(call_id="c1", profile=VoiceProfile(tenant=t), started_at=sunday)
    opened = CallState(call_id="c2", profile=VoiceProfile(tenant=t), started_at=monday_noon)
    assert call_document(closed, final=False)["after_hours"] is True
    assert call_document(opened, final=False)["after_hours"] is False


# ── Reports by e-mail ───────────────────────────────────────

async def test_monthly_report_is_sent_once_on_the_first(db, monkeypatch):
    t = await _tenant()
    await db.tenants.update_one({"_id": t.id}, {"$set": {"created_at": datetime(2026, 10, 1, tzinfo=timezone.utc)}})
    t = TenantConfig.from_doc(await db.tenants.find_one({"_id": t.id}))
    await _calls(db, t.id, datetime(2026, 11, 20, tzinfo=timezone.utc), [{"outcome": "booked", "after_hours": True}])
    sent = []

    async def fake_send(to, subject, text):
        sent.append((to, subject, text))
        return True

    monkeypatch.setattr(tenant_jobs, "send_email", fake_send)
    assert await tenant_jobs.maybe_send_monthly_report(t, NOW) is True
    assert await tenant_jobs.maybe_send_monthly_report(t, NOW + timedelta(hours=2)) is False   # claimed for 2026-11
    assert len(sent) == 1 and "November" in sent[0][1] and "1 calls answered" in sent[0][2]
    assert await tenant_jobs.maybe_send_monthly_report(t, NOW + timedelta(days=1)) is False    # not the 1st

    off = t.model_copy(update={"notifications": t.notifications.model_copy(update={"monthly_report": False})})
    await db.tenants.update_one({"_id": t.id}, {"$unset": {"jobs": ""}})
    assert await tenant_jobs.maybe_send_monthly_report(off, NOW) is False


async def test_no_monthly_report_for_a_business_that_just_signed_up(db, monkeypatch):
    t = await _tenant()
    t = t.model_copy(update={"created_at": datetime(2026, 12, 1, 9, 0, tzinfo=timezone.utc)})
    monkeypatch.setattr(tenant_jobs, "send_email", lambda *a: pytest.fail("should not send"))
    assert await tenant_jobs.maybe_send_monthly_report(t, NOW) is False


async def test_pilot_report_goes_out_on_mondays_to_pilots_only(db, monkeypatch):
    monkeypatch.setattr(settings, "PILOT_REPORT_BCC", "me@example.com")
    t = await _tenant(pilot={"active": True})
    plain = await _tenant("ten_B")
    sent = []

    async def fake_send(to, subject, text):
        sent.append(to)
        return True

    monkeypatch.setattr(tenant_jobs, "send_email", fake_send)
    monday = datetime(2026, 11, 23, 16, 0, tzinfo=timezone.utc)
    assert await tenant_jobs.maybe_send_pilot_report(plain, monday) is False
    assert await tenant_jobs.maybe_send_pilot_report(t, monday - timedelta(days=1)) is False   # Sunday
    assert await tenant_jobs.maybe_send_pilot_report(t, monday) is True
    assert await tenant_jobs.maybe_send_pilot_report(t, monday + timedelta(hours=3)) is False  # once a week
    assert sent == ["owner@salon.com", "me@example.com"]


# ── Pilots, transcript review, eval cases ───────────────────

async def _admin(client):
    await auth_service.create_user(email="root@platform.io", password=PASSWORD, platform_admin=True)
    return await _login(client, "root@platform.io")


async def test_pilot_flag_is_platform_only(db, client):
    await _tenant()
    await auth_service.create_user(email="owner@a.com", password=PASSWORD, tenant_id="ten_A", role="owner")
    owner = await _login(client, "owner@a.com")
    r = client.patch("/t/settings", json={"pilot": {"active": True}}, headers=_h(owner))
    assert r.status_code == 422
    assert client.patch("/t/settings", json={"answering_mode": "overflow"}, headers=_h(owner)).status_code == 200

    root = await _admin(client)
    r = client.patch("/platform/tenants/ten_A", json={"pilot": {"active": True, "contact_name": "Ann"}}, headers=_h(root))
    assert r.status_code == 200, r.text
    pilots = client.get("/platform/pilots", headers=_h(root)).json()["pilots"]
    assert [p["id"] for p in pilots] == ["ten_A"] and pilots[0]["answering_mode"] == "overflow"
    assert pilots[0]["last_7_days"]["calls_answered"] == 0


def test_sampling_is_deterministic():
    picks = [reviews.in_sample(f"call_{i}", 20) for i in range(1000)]
    assert picks == [reviews.in_sample(f"call_{i}", 20) for i in range(1000)]
    assert 120 < sum(picks) < 280
    assert reviews.in_sample("x", 100) and not reviews.in_sample("x", 0)


def test_redaction():
    text = "I'm Maria Lopez, call me on +1 (512) 555-0111 or maria.l@gmail.com, I live at 42 Oak Street, card 4111111111111111"
    out = reviews.redact(text, names=["Maria Lopez"])
    for leaked in ("Maria", "Lopez", "555-0111", "gmail", "42 Oak", "4111"):
        assert leaked not in out, out
    assert "Alex" in out and "Oak Street" in out


async def test_review_flow_to_an_exported_eval_case(db, client, monkeypatch):
    monkeypatch.setattr(settings, "REVIEW_SAMPLE_PCT", 0)
    pilot = await _tenant("ten_P", pilot={"active": True})
    await _tenant("ten_N")
    now = datetime.now(timezone.utc)
    await Repos(db, pilot.id).contacts.upsert_by_phone("+15125550111", {"name": "Maria Lopez"})
    await _calls(db, pilot.id, now, [{"outcome": "message", "caller_phone": "+15125550111",
                                      "transcript": [{"role": "assistant", "text": "Hi, Bella Salon."},
                                                     {"role": "user", "text": "Hi it's Maria, how much is keratin?"},
                                                     {"role": "user", "text": "My number is 512 555 0111"}]}])
    await _calls(db, "ten_N", now, [{"outcome": "faq"}])
    root = await _admin(client)

    queue = client.get("/platform/reviews/queue", headers=_h(root)).json()["calls"]
    assert [c["tenant_id"] for c in queue] == ["ten_P"] and queue[0]["pilot"] is True   # 0 % sample of others
    call_id = queue[0]["call_id"]
    one = client.get(f"/platform/reviews/calls/ten_P/{call_id}", headers=_h(root)).json()
    assert len(one["call"]["transcript"]) == 3 and one["review"] is None

    r = client.post("/platform/reviews", json={"tenant_id": "ten_P", "call_id": call_id, "verdict": "issue",
                                               "issues": ["hallucination", "nonsense"], "notes": "Quoted a price"},
                    headers=_h(root))
    assert r.status_code == 200 and r.json()["review"]["issues"] == ["hallucination"]
    assert client.get("/platform/reviews/queue", headers=_h(root)).json()["calls"] == []

    r = client.post("/platform/eval-cases", json={
        "tenant_id": "ten_P", "call_id": call_id, "title": "Unknown price", "must_not_call": ["book_appointment"],
        "expected": "Does not quote a price for keratin; offers a call back."}, headers=_h(root))
    assert r.status_code == 200, r.text
    case = r.json()["case"]
    assert case["status"] == "draft" and len(case["caller_turns"]) == 2
    assert "Maria" not in str(case["caller_turns"]) and "0111" not in str(case["caller_turns"])
    assert "tenant_id" not in case and "source_call_id" not in case
    assert client.get("/platform/eval-cases/export", headers=_h(root)).json()["cases"] == []

    assert client.patch(f"/platform/eval-cases/{case['id']}", json={"status": "approved"}, headers=_h(root)).status_code == 200
    exported = client.get("/platform/eval-cases/export", headers=_h(root)).json()["cases"]
    assert [c["title"] for c in exported] == ["Unknown price"]
    from tests.evals.test_case_evals import load_case  # noqa: F401  (the same shape the runner reads)

    stats = client.get("/platform/reviews/stats", headers=_h(root)).json()
    assert stats["by_tenant"]["ten_P"] == {"reviewed": 1, "issues": 1} and stats["eval_cases"]["approved"] == 1


async def test_eval_case_needs_a_transcript(db, client):
    t = await _tenant()
    await Repos(db, t.id).calls.insert_one({"call_id": "purged", "status": "completed", "started_at": datetime.now(timezone.utc)})
    root = await _admin(client)
    r = client.post("/platform/eval-cases", json={"tenant_id": t.id, "call_id": "purged", "title": "x" * 5,
                                                  "expected": "Something good happens."}, headers=_h(root))
    assert r.status_code == 404


# ── Margin per business ─────────────────────────────────────

async def test_margin_per_business(db, client):
    from core.billing import plans

    await _tenant("ten_paid", billing={"plan": "starter"}, limits=plans.limits_for(plans.PLANS["starter"]))
    await _tenant("ten_manual")
    await _tenant("ten_trial", billing={"plan": "growth"})
    await db.tenants.update_one({"_id": "ten_trial"}, {"$set": {"status": "trial"}})
    now = datetime.now(timezone.utc)
    for tid, seconds, cost in (("ten_paid", 200 * 60, 40.0), ("ten_manual", 600, 1.5), ("ten_trial", 1200, 3.0)):
        await Repos(db, tid).usage.insert_one({"call_id": f"{tid}_u", "kind": "voice_minute", "seconds": seconds,
                                               "cost_usd": cost, "billable": True, "at": now - timedelta(days=2)})
    root = await _admin(client)
    out = client.get("/platform/margins", headers=_h(root)).json()
    rows = {r["id"]: r for r in out["businesses"]}
    paid = rows["ten_paid"]
    # $69 plan + 50 overage minutes × $0.25 = $81.50 revenue; $40 usage + $3 fixed cost
    assert paid["revenue_usd"] == 81.5 and paid["cost_usd"] == 43.0 and paid["margin_usd"] == 38.5
    assert rows["ten_manual"]["revenue_usd"] is None and rows["ten_manual"]["margin_usd"] is None
    assert rows["ten_trial"]["revenue_usd"] == 0.0 and rows["ten_trial"]["flag"] == ""      # trials aren't flagged
    assert out["totals"]["revenue_usd"] == 81.5


async def test_platform_endpoints_need_a_platform_admin(db, client):
    await _tenant()
    await auth_service.create_user(email="owner@a.com", password=PASSWORD, tenant_id="ten_A", role="owner")
    owner = await _login(client, "owner@a.com")
    for path in ("/platform/pilots", "/platform/reviews/queue", "/platform/margins", "/platform/eval-cases/export",
                 "/platform/privacy/requests", "/platform/consent-events"):
        assert client.get(path, headers=_h(owner)).status_code == 403, path
