"""Legal documents edited in the platform and published on the website (core/compliance/legal_docs.py)."""

from datetime import date

import pytest

from config import settings
from core.auth import service as auth_service
from core.compliance import legal, legal_docs
from core.services.tenant_admin import create_tenant
from core.tenancy import invalidate

PASSWORD = "correct horse battery"


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


async def _admin(client):
    await auth_service.create_user(email="root@platform.io", password=PASSWORD, platform_admin=True)
    return _h(await _login(client, "root@platform.io"))


def test_rendering_escapes_html_and_refuses_script_links():
    html = legal_docs.render("# Hi\n\n<script>alert(1)</script>\n\n[x](javascript:alert(1))\n\n| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert 'href="javascript' not in html
    assert "<table>" in html


def test_placeholders_are_brackets_but_not_link_text():
    md = "See our [Privacy Policy](/legal/privacy). Registered at [registered address], cell [ ]."
    assert legal_docs.placeholders(md) == ["[registered address]", "[ ]"]


def test_next_version_is_today_or_one_more_than_the_last():
    today = date(2026, 11, 24)
    assert legal_docs.next_version(today, "2026-11-19") == "2026-11-24"
    assert legal_docs.next_version(today, "2026-11-24") == "2026-11-24.2"
    assert legal_docs.next_version(today, "2026-11-24.2") == "2026-11-24.3"
    assert legal_docs.next_version(today, "2099-01-01") > "2099-01-01"


def test_seed_texts_fill_in_the_servers_own_values():
    md = legal_docs.seed_text("subscription")
    assert f"{settings.TRIAL_DAYS}-day pilot" in md and "{{" not in md
    assert "Dental Essentials" in md and "A$549" in md and "£279" in md
    terms = legal_docs.seed_text("terms")
    assert "DRAFT for counsel" not in terms and "(/legal/aup)" in terms
    assert f"{settings.TRIAL_DAYS} days or {settings.TRIAL_MINUTES} minutes" in terms
    assert "[registered address]" in terms                     # what only the business can decide stays marked


async def test_seeding_keeps_existing_versions_and_publishes_only_finished_texts(db):
    await legal_docs.ensure_seeded(db)
    await legal_docs.ensure_seeded(db)                         # idempotent
    assert await db[legal_docs.LEGAL_DOCS].count_documents({}) == len(legal.DOCUMENTS)
    versions = await legal_docs.current_versions(db)
    assert versions["terms"] == legal.DOCUMENTS["terms"]["version"]     # nobody re-accepts on deploy
    terms = await db[legal_docs.LEGAL_DOCS].find_one({"_id": "terms"})
    assert terms["published"] is None and "[registered address]" in terms["draft_md"]


async def test_publishing_a_material_change_asks_users_to_accept_again(db):
    await legal_docs.save_draft(db, "terms", title="Terms of Service", md="# Terms\n\nAll filled in.", by="u1")
    first = await legal_docs.publish(db, "terms", material=False, summary="First", by="u1", today=date(2026, 11, 24))
    assert first["version"] == legal.DOCUMENTS["terms"]["version"]       # first publication keeps the version
    user = {"legal_acceptances": legal.acceptance_rows(legal.SIGNUP_DOCUMENTS, versions=await legal.load_versions(db))}
    assert legal.outstanding(user, versions=await legal.load_versions(db)) == []

    await legal_docs.save_draft(db, "terms", title="Terms of Service", md="# Terms\n\nTypo fixed.", by="u1")
    typo = await legal_docs.publish(db, "terms", material=False, summary="Typo", by="u1", today=date(2026, 11, 25))
    assert typo["version"] == first["version"] and typo["revision"] == 2
    assert legal.outstanding(user, versions=await legal.load_versions(db)) == []

    await legal_docs.save_draft(db, "terms", title="Terms of Service", md="# Terms\n\nNew liability cap.", by="u1")
    big = await legal_docs.publish(db, "terms", material=True, summary="Cap", by="u1", today=date(2026, 11, 26))
    assert big["version"] == "2026-11-26"
    assert legal.outstanding(user, versions=await legal.load_versions(db)) == ["terms"]
    assert await db[legal_docs.LEGAL_VERSIONS].count_documents({"key": "terms"}) == 3   # every text kept


async def test_a_document_with_placeholders_cannot_be_published(db):
    await legal_docs.save_draft(db, "privacy", title="Privacy Policy", md="Contact [legal e-mail].", by="u1")
    with pytest.raises(legal_docs.LegalDocError) as e:
        await legal_docs.publish(db, "privacy", material=True, summary="", by="u1")
    assert "[legal e-mail]" in str(e.value)


async def test_platform_edits_and_the_website_reads(db, client):
    admin = await _admin(client)
    assert client.get("/public/legal/documents/terms").status_code == 404           # not published yet
    listed = client.get("/platform/legal/documents", headers=admin)
    assert listed.status_code == 200
    keys = {d["key"] for d in listed.json()["documents"]}
    assert {"terms", "privacy", "subscription", "cookies"} <= keys

    r = client.put("/platform/legal/documents/terms/draft", headers=admin,
                   json={"title": "Terms of Service", "markdown": "## 1. Service\n\nWe answer your phone."})
    assert r.status_code == 200 and r.json()["unpublished_changes"] is True
    r = client.post("/platform/legal/documents/terms/publish", headers=admin, json={"material": True, "summary": "First"})
    assert r.status_code == 200, r.text

    page = client.get("/public/legal/documents/terms")
    assert page.status_code == 200 and "<h2>1. Service</h2>" in page.json()["html"]
    assert "max-age" in page.headers["cache-control"]
    docs = {d["key"]: d for d in client.get("/public/legal").json()["documents"]}
    assert docs["terms"]["published"] is True and docs["aup"]["published"] is False

    preview = client.post("/platform/legal/preview", headers=admin, json={"markdown": "Fill [this]"})
    assert preview.json()["placeholders"] == ["[this]"]
    bad = client.put("/platform/legal/documents/terms/draft", headers=admin, json={"markdown": "Fill [this]"})
    assert bad.status_code == 200
    assert client.post("/platform/legal/documents/terms/publish", headers=admin, json={}).status_code == 422


async def test_only_platform_admins_edit(db, client):
    t = await create_tenant(name="Practice", vertical="dental", tenant_id="ten_A")
    await auth_service.create_user(email="owner@a.com", password=PASSWORD, tenant_id=t.id, role="owner")
    owner = _h(await _login(client, "owner@a.com"))
    assert client.get("/platform/legal/documents", headers=owner).status_code == 403
    assert client.put("/platform/legal/documents/terms/draft", headers=owner, json={"markdown": "x"}).status_code == 403


def test_links_default_to_the_marketing_site(monkeypatch):
    monkeypatch.setattr(settings, "LEGAL_SITE_URL", "https://laudline.example/")
    monkeypatch.setattr(settings, "TERMS_URL", "")
    monkeypatch.setattr(settings, "PRIVACY_URL", "https://elsewhere.example/privacy")
    assert legal.url_for("terms") == "https://laudline.example/legal/terms"
    assert legal.url_for("privacy") == "https://elsewhere.example/privacy"          # an explicit URL wins
