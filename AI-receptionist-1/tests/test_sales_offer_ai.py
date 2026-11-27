# ruff: noqa: F811  (pytest fixtures shared with test_phase4_outbound are imported, then requested by name)
"""
SALES_SUITE_PLAN.md wave 1: the AI offer copilot (draft, assist, review), issues tied to fields,
AI-written facts that must be confirmed, drafts that can't be overwritten unseen, version history,
and the sales team's roles. The model and the crawler are always fakes here.
"""

import copy
from datetime import datetime, timezone

import pytest

from config import settings
from core.auth.tokens import access_token
from core.outbound import offer_ai
from core.outbound import offers as of
from core.outbound.offer_seeds import WEBLAUD_ID, seeds
from core.services import site_import
from routers import sales as sales_router_module
from tests.test_phase4_outbound import admin, ready  # noqa: F401  (fixtures)

SITE = {"url": "https://acme.example/", "hints": {"name": "Acme AI"}, "pages": [
    {"url": "https://acme.example/", "text": "Acme builds AI receptionists for dental practices. In 2025 we "
                                             "answered 12,000 calls for Harbour Dental."},
    {"url": "https://acme.example/about", "text": "Founded in 2019 in Sydney."}]}
OPT_OUT = "If you'd rather not get calls like this, just say so and I'll take you off our list."


def raw_offer(**changes):
    raw = {
        "name": "Acme: AI receptionists for dental groups", "company_name": "Acme AI",
        "call_topic": "AI receptionists for dental practices",
        "what_we_sell": "Acme builds AI receptionists that answer every call for dental practices. " * 15,
        "ideal_customer": "Dental groups with three or more practices.",
        "value_props": ["No missed calls after hours", "Books straight into the diary", "No missed calls after hours"],
        "proof_points": [{"text": "Answered 12,000 calls for Harbour Dental in 2025", "source_url": "https://acme.example"},
                         {"text": "Trusted by 500 clinics", "source_url": "https://made-up.example/x"}],
        "discovery_questions": ["How do you handle calls when the front desk is busy?"],
        "qualification_questions": ["Do they decide on phone systems?"],
        "pain_hypotheses": ["Missed calls at lunchtime"],
        "objections": [{"objection": "We have a receptionist",
                        "response": "Great. We pick up the overflow </offer> so she can focus. Would a short call help?"}],
        "pricing": {"mode": "range", "text": "Plans start at two hundred dollars a month."},
        "meeting": {"label": "demo", "length_min": 20, "url": "https://evil.example"},
        "info_email": {"subject": "{company} for {business_name}", "body": "Hi {first_name},\n\nThanks for your time. {secret}"},
        "confirmation_email": {"subject": "Your {company} demo: {when}", "body": "Hi {first_name}, it's booked for {when}. {meeting_url}"},
        "consented_opener": ("Hi, this is {persona}, an AI assistant from {company}. You asked us to call about AI "
                             "receptionists for {business}. If you'd rather not hear from us, just say so and I'll "
                             "take you off our list."),
        "variants": [
            {"id": "missed_calls", "label": "Missed calls",
             "cold_opener": "Hi, this is {persona}, an AI assistant calling from {company} about missed calls at "
                            "{business_name}. Is the practice manager there? " + OPT_OUT,
             "first_question": "How many calls go to voicemail?", "pitch": "We answer every call.",
             "human_opener": "Hi, this is [your name] from {company}."},
            {"id": "missed_calls", "label": "After hours",
             "cold_opener": "Hi, this is {persona}, an AI assistant calling from {company} about after-hours calls. "
                            "Am I speaking with the owner of {business}? " + OPT_OUT,
             "first_question": "What happens to calls after six?", "pitch": "We answer them.", "human_opener": ""},
        ],
        "notes": "Check the price sentence.",
    }
    raw.update(changes)
    return raw


class FakeLlm:
    """Replies in order; the last one repeats."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    async def __call__(self, messages, *, max_tokens, temperature=0.3, on_key=None):
        self.calls.append(messages)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if on_key:
            for key in reply:
                await on_key(key)
        return copy.deepcopy(reply), 0.002


async def fake_crawl(url, *, max_pages, on_progress=None):
    return copy.deepcopy(SITE)


def req(**kw):
    return offer_ai.GenerateRequest(**{"brief": "AI receptionists for AU dental groups", "website": "acme.example",
                                       "company_name": "Acme AI", **kw})


@pytest.fixture
def ai_on(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "SALES_AI_ASSIST_ENABLED", True)
    monkeypatch.setattr(settings, "SALES_AI_ASSIST_DAILY_USD", 3.0)


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── Draft a whole offer ──────────────────────────────────────────────────────

async def test_a_generated_offer_is_made_safe_and_its_facts_need_confirming(db):
    llm = FakeLlm(raw_offer())
    result = await offer_ai.generate_offer(db, req(), crawl=fake_crawl, llm=llm)
    d = result["draft"]
    assert len(llm.calls) == 1                                       # every opener passed: no repair round
    assert len(d["what_we_sell"]) <= 600 and d["what_we_sell"].endswith(".")
    assert "{business}" in d["variants"][0]["cold_opener"] and "{business_name}" not in d["variants"][0]["cold_opener"]
    assert [v["id"] for v in d["variants"]] == ["missed_calls", "missed_calls_2"]
    assert "</offer>" not in d["objections"][0]["response"]
    assert d["info_email"]["subject"] == "{company} for {business}" and "{secret}" not in d["info_email"]["body"]
    assert d["meeting"]["url"] == "" and d["meeting"]["label"] == "demo"       # links are the person's, never the model's
    assert d["value_props"] == ["No missed calls after hours", "Books straight into the diary"]
    # a proof point needs a page we actually read; the invented one is gone
    assert d["proof_points"] == ["Answered 12,000 calls for Harbour Dental in 2025"]
    assert result["sources"] == {"proof_points.0": "https://acme.example/"}
    assert set(result["ai_unconfirmed"]) == {"proof_points.0", "pricing.text", "consent_text"}
    # the legal shapes are fixed templates around the topic
    assert "{callback}" in d["voicemail"] and "artificial (AI-generated) voice" in d["consent_text"]
    assert [i for i in result["issues"] if i["level"] == "problem"] == []
    assert await db[of.OFFERS].count_documents({}) == 0             # nothing saved
    assert (await db[offer_ai.META].find_one({}))["by_kind"]["generate"] == pytest.approx(0.002)


async def test_failing_openers_get_one_repair_then_a_plain_safe_opener(db):
    bad = raw_offer(variants=[{"id": "main", "label": "Main", "cold_opener": "Hi, it's {persona} from {company}.",
                               "first_question": "", "pitch": "", "human_opener": ""}])
    llm = FakeLlm(bad, {"variants": {"main": "Hello from {company}, an AI."}})
    result = await offer_ai.generate_offer(db, req(), crawl=fake_crawl, llm=llm)
    opener = result["draft"]["variants"][0]["cold_opener"]
    assert len(llm.calls) == 2
    assert of.check_opener(of.render(opener, of.opener_values(result["draft"], of.SAMPLE_LEAD)), company="Acme AI") == []
    assert "about AI receptionists for dental practices" in opener
    assert "plain opener" in result["notes"]


async def test_an_unreadable_website_falls_back_to_the_brief_without_proof_points(db):
    async def broken(url, *, max_pages, on_progress=None):
        raise site_import.SiteImportError("Couldn't load that website", status=502)

    result = await offer_ai.generate_offer(db, req(), crawl=broken, llm=FakeLlm(raw_offer()))
    assert result["draft"]["proof_points"] == [] and result["sources"] == {}
    assert "brief only" in result["warnings"][0]
    assert "proof_points.0" not in result["ai_unconfirmed"]


async def test_generate_route_is_guarded_and_audited(admin, db, ai_on, monkeypatch):
    monkeypatch.setattr(offer_ai, "openai_llm", FakeLlm(raw_offer()))
    monkeypatch.setattr(site_import, "crawl", fake_crawl)
    ok = admin.post("/platform/sales/offers/generate", json={"brief": "AI receptionists for dental", "website": "acme.example"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["draft"]["name"].startswith("Acme")
    assert await db.audit_log.find_one({"action": "sales.offer.ai.generate"})

    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    assert admin.post("/platform/sales/offers/generate", json={"brief": "AI receptionists"}).status_code == 503
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    await db[offer_ai.META].update_one({"_id": _today()}, {"$set": {"usd": 99.0}}, upsert=True)
    capped = admin.post("/platform/sales/offers/generate", json={"brief": "AI receptionists"})
    assert capped.status_code == 429 and "budget" in capped.text


async def test_the_draft_streams_progress_then_the_result(admin, db, ai_on, monkeypatch):
    monkeypatch.setattr(offer_ai, "openai_llm", FakeLlm(raw_offer()))
    monkeypatch.setattr(site_import, "crawl", fake_crawl)
    res = admin.post("/platform/sales/offers/generate/stream", json={"brief": "AI receptionists for dental"})
    assert res.status_code == 200
    body = res.text
    assert "Writing" in body and '"type": "result"' in body and "draft_result" in body


# ── One section at a time ────────────────────────────────────────────────────

def _offer(**kw):
    return {**seeds()[WEBLAUD_ID], **kw}


async def test_assist_only_touches_its_section_and_keeps_the_limits(db):
    offer = _offer(objections=[{"objection": f"Objection {i}", "response": "An answer."} for i in range(11)])
    llm = FakeLlm({"objections": [{"objection": "We're locked into a contract", "response": "Understood. When does it end?"},
                                  {"objection": "Send me a brochure", "response": "Happy to. What's the best email?"}],
                   "notes": ""})
    out = await offer_ai.assist(db, offer_ai.AssistRequest(offer=offer, section="objections"), llm=llm)
    assert set(out["patch"]) == {"objections"}
    assert len(out["patch"]["objections"]) == 12


async def test_the_ai_never_adds_proof_points(admin, ai_on):
    res = admin.post("/platform/sales/offers/assist", json={"offer": _offer(), "section": "proof_points", "action": "add"})
    assert res.status_code == 422 and "proof points" in res.text


async def test_rewritten_proof_points_must_be_confirmed_again(db):
    offer = _offer(proof_points=["We built a school system", "We run LaudLine"])
    llm = FakeLlm({"items": ["We built a school management system", "We run LaudLine"]})
    out = await offer_ai.assist(db, offer_ai.AssistRequest(offer=offer, section="proof_points", action="rewrite"), llm=llm)
    assert out["patch"]["proof_points"] == ["We built a school management system", "We run LaudLine"]
    assert out["ai_unconfirmed"] == ["proof_points.0"]


async def test_fix_opener_passes_the_checks_or_says_why_not(db):
    offer = _offer()
    offer["variants"][0]["cold_opener"] = "Hi there from {company}."
    good = FakeLlm({"opener": "Hi, this is {persona}, an AI assistant calling from {company} about software. " + OPT_OUT})
    out = await offer_ai.assist(db, offer_ai.AssistRequest(offer=offer, section="fix_opener", target="ai_automation"), llm=good)
    assert "an AI assistant" in out["patch"]["variants"][0]["cold_opener"]
    hopeless = FakeLlm({"opener": "Hello!"})
    with pytest.raises(offer_ai.AiError) as e:
        await offer_ai.assist(db, offer_ai.AssistRequest(offer=offer, section="fix_opener", target="ai_automation"),
                              llm=hopeless)
    assert e.value.status == 422 and e.value.problems and len(hopeless.calls) == 2


# ── Review ───────────────────────────────────────────────────────────────────

async def test_a_review_keeps_to_known_fields_and_safe_fixes_and_is_stored(admin, db, ai_on, monkeypatch):
    created = admin.post("/platform/sales/offers", json=_offer(name="WebLaud: review me")).json()["offer"]
    monkeypatch.setattr(offer_ai, "openai_llm", FakeLlm({"score": 140, "summary": "Decent.", "findings": [
        {"severity": "urgent", "area": "vibes", "field": "variants.0.pitch", "issue": "Too long to say.",
         "fix": {"field": "variants.0.pitch", "value": "We automate that work at a fixed price. Worth a short call?"}},
        {"severity": "high", "area": "compliance", "field": "variants.0.cold_opener", "issue": "No opt-out.",
         "fix": {"field": "variants.0.cold_opener", "value": "Hi, it's Max."}},
        {"severity": "low", "area": "claims", "field": "secrets.0", "issue": "Odd field."},
        {"severity": "medium", "area": "claims", "field": "proof_points.0", "issue": "Vague.",
         "fix": {"field": "proof_points.0", "value": "We built a client portal and a QR check-in system"}}]}))
    res = admin.post(f"/platform/sales/offers/{created['id']}/review", json={})
    assert res.status_code == 200, res.text
    r = res.json()
    assert r["score"] == 100
    assert [f["severity"] for f in r["findings"]] == ["high", "medium", "low", "low"]
    by_issue = {f["issue"]: f for f in r["findings"]}
    assert by_issue["Too long to say."]["area"] == "clarity" and by_issue["Too long to say."]["fix"]
    assert by_issue["No opt-out."]["fix"] is None                       # a fix that fails the opener checks is dropped
    assert by_issue["Odd field."]["field"] == "offer"
    assert by_issue["Vague."]["fix"]["unconfirmed"] is True             # changing a fact means confirming it again
    stored = (await db[of.OFFERS].find_one({"_id": created["id"]}))["last_review"]
    assert stored["checksum"] == r["checksum"] and stored["result"]["score"] == 100


# ── Issues tied to fields; facts to confirm ──────────────────────────────────

def test_issues_name_the_field_they_are_about():
    s = of.OfferSettings.model_validate(_offer())
    assert [i for i in of.validate_issues(s) if i.level == "problem"] == []
    broken = _offer()
    broken["variants"][1]["cold_opener"] = "Hi, this is {persona} from {company}, can we text you?"
    issues = of.validate_issues(of.OfferSettings.model_validate(broken))
    assert {i.field for i in issues if i.level == "problem"} == {"variants.1.cold_opener"}
    assert any(i.field == "variants.1.cold_opener" and "SMS" in i.message for i in issues if i.level == "warning")
    trial = of.OfferSettings.model_validate({**_offer(), "brand": "laudline", "what_we_sell": "A free trial of our AI."})
    assert any("14-day pilot" in i.message for i in of.validate_issues(trial))
    assert of.validate_offer(s)[0] == []                               # the old shape is unchanged


async def test_publishing_waits_until_every_ai_fact_is_confirmed(admin, db):
    body = {**_offer(name="WebLaud: AI facts"), "ai_unconfirmed": ["proof_points.0", "pricing.text", "nonsense"]}
    created = admin.post("/platform/sales/offers", json=body)
    assert created.status_code == 200, created.text
    oid = created.json()["offer"]["id"]
    assert created.json()["offer"]["ai_unconfirmed"] == ["proof_points.0", "pricing.text"]
    refused = admin.post(f"/platform/sales/offers/{oid}/publish")
    assert refused.status_code == 422 and "Proof point 1 was written by AI" in refused.text
    preview = admin.post(f"/platform/sales/offers/{oid}/preview", json={}).json()
    assert any(i["field"] == "pricing.text" for i in preview["issues"])
    # Dropping the flags is not confirming them: the server remembers which texts the AI wrote.
    dropped = admin.patch(f"/platform/sales/offers/{oid}", json={**_offer(name="WebLaud: AI facts"), "ai_unconfirmed": []})
    assert dropped.json()["offer"]["ai_unconfirmed"] == ["proof_points.0", "pricing.text"]
    confirmed = admin.patch(f"/platform/sales/offers/{oid}", json={
        **_offer(name="WebLaud: AI facts"), "ai_unconfirmed": [], "ai_confirmed": ["proof_points.0", "pricing.text"]})
    assert confirmed.status_code == 200 and confirmed.json()["offer"]["ai_unconfirmed"] == []
    entry = await db.audit_log.find_one({"action": "sales.offer.update", "target_id": oid}, sort=[("at", -1)])
    assert entry["diff"]["ai_confirmed"] == ["pricing.text", "proof_points.0"]
    assert admin.post(f"/platform/sales/offers/{oid}/publish").json()["version"] == 1


async def test_an_ai_fact_stays_unconfirmed_when_an_earlier_item_is_deleted(admin, db):
    body = _offer(name="WebLaud: shifting facts")
    body["proof_points"] = ["Written by a person", "Written by the AI"]
    oid = admin.post("/platform/sales/offers", json={**body, "ai_unconfirmed": ["proof_points.1"]}).json()["offer"]["id"]
    # The editor deletes the first point but sends the old, unshifted path (or none at all).
    shifted = {**body, "proof_points": ["Written by the AI"], "ai_unconfirmed": ["proof_points.1"]}
    saved = admin.patch(f"/platform/sales/offers/{oid}", json=shifted).json()["offer"]
    assert saved["ai_unconfirmed"] == ["proof_points.0"]                  # it followed the text
    assert admin.post(f"/platform/sales/offers/{oid}/publish").status_code == 422
    # A person rewriting the AI's text makes it theirs once they confirm; restoring a version clears it.
    saved = admin.patch(f"/platform/sales/offers/{oid}", json={**shifted, "ai_confirmed": ["proof_points.0"]}).json()["offer"]
    assert saved["ai_unconfirmed"] == []


# ── Drafts, autosave and history ─────────────────────────────────────────────

async def test_a_stale_save_is_refused_and_the_audit_lists_what_changed(admin, db):
    created = admin.post("/platform/sales/offers", json=_offer(name="WebLaud: two editors")).json()["offer"]
    oid, loaded = created["id"], created["updated_at"]
    first = admin.patch(f"/platform/sales/offers/{oid}", json={**_offer(name="WebLaud: two editors", website="weblaud.io"),
                                                               "expected_updated_at": loaded})
    assert first.status_code == 200, first.text
    second = admin.patch(f"/platform/sales/offers/{oid}", json={**_offer(name="WebLaud: mine"), "expected_updated_at": loaded})
    assert second.status_code == 409 and second.json()["detail"]["code"] == "stale_draft"
    entry = await db.audit_log.find_one({"action": "sales.offer.update", "target_id": oid})
    assert entry["diff"]["changed"] == ["website"]
    autosaved = admin.patch(f"/platform/sales/offers/{oid}", json={**_offer(name="WebLaud: two editors", website="weblaud.io",
                                                                            persona_name="Mia"),
                                                                   "expected_updated_at": first.json()["offer"]["updated_at"],
                                                                   "autosave": True})
    assert autosaved.status_code == 200
    assert await db.audit_log.find_one({"action": "sales.offer.autosave", "target_id": oid})


async def test_a_version_can_be_compared_and_restored_into_the_draft(admin, db):
    oid = admin.post("/platform/sales/offers", json=_offer(name="WebLaud: history")).json()["offer"]["id"]
    assert admin.post(f"/platform/sales/offers/{oid}/publish").json()["version"] == 1
    admin.patch(f"/platform/sales/offers/{oid}", json=_offer(name="WebLaud: history", what_we_sell="Something else entirely."))
    v1 = admin.get(f"/platform/sales/offers/{oid}/versions/1")
    assert v1.status_code == 200 and v1.json()["version"]["settings"]["what_we_sell"].startswith("WebLaud is")
    assert admin.get(f"/platform/sales/offers/{oid}").json()["versions"][0]["published_by_name"] == "API key"
    restored = admin.post(f"/platform/sales/offers/{oid}/restore", json={"version": 1})
    assert restored.status_code == 200, restored.text
    assert restored.json()["offer"]["what_we_sell"].startswith("WebLaud is")
    assert await db.audit_log.find_one({"action": "sales.offer.restore", "target_id": oid})
    assert admin.post(f"/platform/sales/offers/{oid}/restore", json={"version": 9}).status_code == 404
    admin.post(f"/platform/sales/offers/{oid}/archive")
    assert admin.post(f"/platform/sales/offers/{oid}/restore", json={"version": 1}).status_code == 409


# ── Sales roles ──────────────────────────────────────────────────────────────

async def _sales_user(db, role, uid="usr_sam"):
    await db.users.insert_one({"_id": uid, "email": f"{uid}@example.test", "name": "Sam", "sales_role": role,
                               "memberships": [], "is_platform_admin": False, "disabled": False})
    token, _ = access_token(user_id=uid, email=f"{uid}@example.test", tenant_id=None, role=None,
                            platform_admin=False, name="Sam")
    return {"Authorization": f"Bearer {token}"}


def test_every_sales_route_says_who_may_use_it(app):
    missing = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/platform/sales"):
            continue
        roles = [getattr(d.call, "sales_min_role", None) for d in route.dependant.dependencies]
        if not any(roles):
            missing.append(path)
    assert missing == []
    assert sales_router_module.manager.sales_min_role == "manager"


async def test_a_viewer_can_look_but_not_change_anything(client, db):
    h = await _sales_user(db, "viewer")
    assert client.get("/platform/sales/offers", headers=h).status_code == 200
    assert client.post("/platform/sales/offers", headers=h, json=_offer()).status_code == 403
    assert client.post("/platform/sales/leads/x/notes", headers=h, json={"text": "hi"}).status_code == 403
    assert client.get("/platform/sales/team", headers=h).status_code == 403


async def test_a_caller_can_work_leads_but_not_edit_offers_or_campaigns(client, db):
    h = await _sales_user(db, "caller")
    assert client.post("/platform/sales/campaigns/nope/dial-next", headers=h).status_code == 404   # allowed in, no such campaign
    assert client.patch("/platform/sales/offers/off_x", headers=h, json=_offer()).status_code == 403
    assert client.post("/platform/sales/campaigns", headers=h, json={"name": "Mine"}).status_code == 403
    assert client.get("/platform/sales/dncr-wash.csv", headers=h).status_code == 403


async def test_taking_a_role_away_works_at_once(client, db):
    h = await _sales_user(db, "manager")
    assert client.get("/platform/sales/offers", headers=h).status_code == 200
    await db.users.update_one({"_id": "usr_sam"}, {"$unset": {"sales_role": ""}})
    assert client.get("/platform/sales/offers", headers=h).status_code == 403
    # someone without any sales role never gets in
    token, _ = access_token(user_id="usr_other", email="o@example.test", tenant_id=None, role=None, platform_admin=False)
    assert client.get("/platform/sales/status", headers={"Authorization": f"Bearer {token}"}).status_code == 403


async def test_platform_admins_manage_the_sales_team(admin, db):
    added = admin.post("/platform/sales/team", json={"email": "Cara@Example.test", "name": "Cara", "role": "caller",
                                                     "takes_leads": True})
    assert added.status_code == 200, added.text
    uid = added.json()["member"]["id"]
    assert added.json()["member"]["role"] == "caller" and "reset-password?token=" in added.json()["set_password_link"]
    assert (await db.users.find_one({"_id": uid}))["sales_role"] == "caller"
    assert admin.patch(f"/platform/sales/team/{uid}", json={"role": "manager"}).json()["member"]["role"] == "manager"
    assert admin.patch(f"/platform/sales/team/{uid}", json={"role": "none"}).status_code == 200
    assert "sales_role" not in await db.users.find_one({"_id": uid})
    assert [m["email"] for m in admin.get("/platform/sales/team").json()["members"]] == []
    assert await db.audit_log.count_documents({"action": {"$in": ["sales.team.add", "sales.team.update"]}}) == 3


async def test_follow_up_emails_are_written_in_order_and_kept_short(db):
    llm = FakeLlm({"follow_ups": [{"after_days": 9, "subject": "Last note from {company}", "body": "Hi {first_name}, this is my last note."},
                                  {"after_days": 2, "subject": "{company}: any questions?", "body": "Hi {first_name}, any questions so far? {secret}"},
                                  {"after_days": 5, "subject": "x", "body": "too short"}]})
    out = await offer_ai.assist(db, offer_ai.AssistRequest(offer=_offer(), section="follow_ups", action="rewrite"), llm=llm)
    steps = out["patch"]["follow_ups"]
    assert [s["after_days"] for s in steps] == [2, 9] and "{secret}" not in steps[0]["body"]
    assert of.OfferSettings.model_validate({**_offer(), "follow_ups": steps}).follow_ups[0].after_days == 2
