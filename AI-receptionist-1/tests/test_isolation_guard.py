"""
CI guard for Roadmap Task 2.3.1: code reaches tenant data only through repositories.

Every module under core/, routers/, agent/, services/ and jobs/ (plus the WhatsApp webhook) is
scanned. Direct `db.<collection>` / `get_db().<collection>` / `db[...]` access is allowed only for:

- platform-level collections, which hold no single business's data (PLATFORM below);
- a line marked `# scoped: <how>`: tenant data with the tenant filter written by hand;
- a line marked `# cross-tenant: <why>`: deliberate access across businesses (platform admin views,
  platform-wide opt-outs, jobs that walk every tenant).

The marker makes every exception visible in review. Handles from `scoped_db()` are named `sdb` so
this check can tell them apart. Legacy hotel modules scoped another way are listed in LEGACY with the
reason; the isolation tests cover them instead.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LEGACY = {
    "core/tools/hotel.py": "HotelToolExecutor._scope adds tenant_id to every query",
    "routers/admin.py": "every handler uses `db = scoped_db(tp.tenant)`",
    "routers/api.py": "every handler uses `db = scoped_db(tp.tenant)`",
}
SCANNED = sorted(
    p for d in ("core", "routers", "agent", "services", "jobs") for p in (ROOT / d).rglob("*.py")
    if "__pycache__" not in p.parts and p.relative_to(ROOT).as_posix() not in LEGACY
) + [ROOT / "whatsapp" / "meta_handler.py"]

PLATFORM = {
    # accounts and routing
    "tenants", "users", "sessions", "invites", "password_resets", "email_verifications", "email_changes",
    "phone_numbers",
    # Phase 5: number orders are retried across tenants by the platform admin (like the routing table);
    # the deletion log must outlive the tenant's own data; Stripe's event log is per Stripe account.
    "number_orders", "deletion_log", "stripe_events",
    # Phase 4: the sales dialer belongs to the platform (your own outbound), not to a customer
    "sales_leads", "campaigns", "call_attempts", "dnc_list", "consent_records", "dnc_registry", "dnc_registry_meta",
    # …and what it sells: offers and every published version of them (SALES_DIALER_PLAN.md)
    "sales_offers", "sales_offer_versions", "sales_test_calls", "sales_research_meta",
    "sales_activity", "sales_tasks", "sales_outbox", "sales_email_suppression", "dnc_registry_coverage",
    # …the AI copilot, safe launch and the built-in CRM (SALES_SUITE_PLAN.md)
    "sales_ai_meta", "sales_offer_simulations", "sales_safety_meta", "sales_number_state", "sales_offer_insights",
    "sales_deals", "sales_views", "sales_custom_fields", "sales_crm_meta", "sales_digest_meta",
    # …and the agent that learns from its calls (SALES_LEARNING_PLAN.md)
    "sales_playbooks", "sales_learning", "sales_experiments", "sales_learning_stats", "sales_learning_meta",
    # Phase 6/7: the platform's consent-revocation evidence, transcript reviews and eval cases
    "consent_events", "call_reviews", "eval_cases",
    # Landing-page demo: visitors who signed in with Google are the platform's own leads, not a business's
    "web_demo_leads",
    # The platform's own legal documents (Terms, Privacy…) and every published version of them
    "legal_documents", "legal_document_versions",
}
# Module constants that name platform collections in `db[...]` access.
PLATFORM_CONSTANTS = {"EVENTS", "optout.EVENTS", "REVIEWS", "CASES", "reviews.REVIEWS", "reviews.CASES",
                      "DEMO_LEADS", "wd.DEMO_LEADS", "LEGAL_DOCS", "LEGAL_VERSIONS",
                      "OFFERS", "OFFER_VERSIONS", "of.OFFERS", "of.OFFER_VERSIONS", "TEST_CALLS", "offers.TEST_CALLS", "META",
                      "ACTIVITY", "TASKS", "OUTBOX", "SUPPRESSION", "followup.ACTIVITY", "followup.TASKS",
                      "followup.OUTBOX", "followup.SUPPRESSION",
                      "DEALS", "VIEWS", "FIELDS", "crm.DEALS", "crm.VIEWS", "crm.FIELDS", "CACHE", "STATE", "SIMULATIONS",
                      "simulate.SIMULATIONS", "offer_ai.META", "DIGEST_META",
                      "PLAYBOOKS", "LEARNING", "EXPERIMENTS", "pb.PLAYBOOKS", "pb.LEARNING", "learning.EXPERIMENTS",
                      "timing.STATS", "bandit.STATS", "STATS", "LEARNING_META", "offer_ai.LEARNING_META", "meta"}

OPS = (r"find|find_one|insert_one|insert_many|update_one|update_many|delete_one|delete_many|count_documents|"
       r"aggregate|distinct|find_one_and_update|replace_one|bulk_write|estimated_document_count")
DIRECT = re.compile(rf"\b(?:get_db\(\)|\bdb|self\.db)\.(\w+)\.(?:{OPS})\b")
SUBSCRIPT = re.compile(rf"\b(?:get_db\(\)|\bdb|self\.db)\[([^\]]+)\]\.(?:{OPS})\b")
MARKER = re.compile(r"#\s*(scoped|cross-tenant):\s*\S")


def _allowed(name: str) -> bool:
    name = name.strip()
    return name in PLATFORM or name in PLATFORM_CONSTANTS or name.startswith("m.")   # core.outbound.models


def _offenders(path: Path) -> list:
    out = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for n, line in enumerate(lines, 1):
        hits = [m.group(1) for m in DIRECT.finditer(line)] + [m.group(1) for m in SUBSCRIPT.finditer(line)]
        bad = [h for h in hits if not _allowed(h)]
        if not bad:
            continue
        above = lines[n - 2] if n > 1 else ""
        if MARKER.search(line) or (above.strip().startswith("#") and MARKER.search(above)):
            continue
        out.append(f"{path.relative_to(ROOT).as_posix()}:{n}: {line.strip()}")
    return out


def test_tenant_data_goes_through_repositories_or_is_marked():
    offenders = [o for path in SCANNED for o in _offenders(path)]
    assert not offenders, (
        "Use core.repositories (tenant-scoped), or mark the line `# scoped: <filter>` / "
        "`# cross-tenant: <reason>`:\n" + "\n".join(offenders))


def test_the_guard_covers_the_whole_backend():
    """New packages must not slip past the scan."""
    scanned = {p.relative_to(ROOT).as_posix() for p in SCANNED}
    for must in ("core/billing/usage.py", "core/compliance/subjects.py", "core/services/offboarding.py",
                 "routers/billing.py", "routers/compliance.py", "routers/launch.py", "routers/sales.py",
                 "routers/tenant.py", "routers/platform.py", "agent/persistence.py", "services/tenant_jobs.py",
                 "services/reminders.py", "whatsapp/meta_handler.py"):
        assert must in scanned, must
    for legacy in LEGACY:
        assert (ROOT / legacy).exists(), f"{legacy} is gone: remove it from LEGACY"


def test_the_guard_catches_unscoped_access(tmp_path):
    bad = tmp_path / "x.py"
    bad.write_text("async def f(db):\n    await db.appointments.find_one({'_id': 1})\n"
                   "    await db['contacts'].delete_many({})\n", encoding="utf-8")
    ok = tmp_path / "y.py"
    ok.write_text("async def f(db, t):\n    await db.contacts.find_one({'tenant_id': t})  # scoped: tenant_id\n"
                  "    # cross-tenant: platform report over every business\n    await db.usage_events.aggregate([])\n"
                  "    await db.tenants.find_one({})\n    await db[m.LEADS].find_one({})\n", encoding="utf-8")
    global ROOT
    saved, ROOT = ROOT, tmp_path
    try:
        assert len(_offenders(bad)) == 2 and _offenders(ok) == []
    finally:
        ROOT = saved


def test_platform_calls_aggregate_is_the_only_cross_tenant_read():
    # /platform/tenants aggregates calls across tenants on purpose (it groups by tenant_id).
    text = (ROOT / "routers" / "platform.py").read_text(encoding="utf-8")
    assert text.count("db.calls.") == 1 and '"_id": "$tenant_id"' in text
