"""Tenant model, resolver cache/legacy mode, and repository isolation (Roadmap Tasks 2.2–2.4)."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from core.repositories import Repos, TenantRepo
from core.tenancy import (
    TenantConfig, invalidate, legacy_hotel_tenant, new_id, resolve_by_id, resolve_by_phone_number,
    resolve_by_slug, resolve_by_whatsapp_phone_number_id,
)
from core.tenancy import resolver
from core.tenancy.models import HoursEntry


@pytest.fixture(autouse=True)
def _fresh_cache():
    invalidate()
    yield
    invalidate()


def _doc(tenant_id="ten_a", slug="bella", **kw):
    d = {"_id": tenant_id, "name": "Bella Dental", "slug": slug, "vertical": "clinic", "timezone": "America/Chicago",
         "integrations": {"whatsapp": {"phone_number_id": f"pnid-{tenant_id}"}}}
    d.update(kw)
    return d


# ── Model ─────────────────────────────────────────────────

def test_ids_are_prefixed_sortable_and_unique():
    a, b = new_id("ten"), new_id("ten")
    assert a.startswith("ten_") and len(a) == 30 and a != b


def test_greeting_discloses_ai_and_recording():
    t = TenantConfig.from_doc(_doc())
    assert t.greeting_text() == "Thanks for calling Bella Dental, this is Aria, Bella Dental's virtual assistant. How can I help?"
    t.agent.recording.enabled = True
    assert "may be recorded" in t.greeting_text()
    t.agent.greeting = "Hi, you've reached Bella Dental. I'm Aria, an AI assistant. What can I do for you?"
    assert t.greeting_text().count("?") == 1 and "recorded" in t.greeting_text()


def test_custom_greeting_without_ai_disclosure_gets_one():
    """⚖ An owner can't remove the AI disclosure by writing their own greeting."""
    t = TenantConfig.from_doc(_doc())
    t.agent.greeting = "Thanks for calling Bella Dental. How can I help you today?"
    assert t.greeting_text() == ("Thanks for calling Bella Dental. This is Aria, Bella Dental's virtual assistant. "
                                 "How can I help you today?")
    t.agent.greeting = "Bella Dental, how can I help?"                 # opens with a question → disclosure first
    assert t.greeting_text().startswith("This is Aria, Bella Dental's virtual assistant. Bella Dental, how")
    t.agent.greeting = "Hello and welcome to Bella Dental"               # no punctuation at all
    assert "virtual assistant" in t.greeting_text() and t.greeting_text().endswith("How can I help?")
    for honest in ("Hi, I'm Aria, Bella Dental's AI receptionist. How can I help?",
                   "You've reached Bella Dental's automated assistant. What can I do for you?",
                   "Hi, this is Aria, a virtual receptionist for Bella Dental. How can I help?"):
        t.agent.greeting = honest
        assert t.greeting_text() == honest                                 # already honest: left as written
    # "said" contains "ai" but isn't a disclosure
    t.agent.greeting = "As we said, thanks for calling. How can I help?"
    assert "virtual assistant" in t.greeting_text()


def test_is_open_handles_overnight_and_closed_days():
    t = TenantConfig.from_doc(_doc(hours=[{"dow": 4, "open": "20:00", "close": "02:00"}]))
    tz = ZoneInfo("America/Chicago")
    assert t.is_open(datetime(2026, 11, 20, 23, 0, tzinfo=tz))        # Friday night
    assert t.is_open(datetime(2026, 11, 21, 1, 0, tzinfo=tz))         # Saturday 01:00 — still Friday's shift
    assert not t.is_open(datetime(2026, 11, 21, 3, 0, tzinfo=tz))
    assert not t.is_open(datetime(2026, 11, 19, 23, 0, tzinfo=tz))    # Thursday: closed


def test_transfer_only_during_hours():
    t = TenantConfig.from_doc(_doc(hours=[{"dow": 0, "open": "09:00", "close": "17:00"}],
                                   transfer={"number": "+15125550100", "only_during_hours": True}))
    tz = ZoneInfo("America/Chicago")
    assert t.transfer_open(datetime(2026, 11, 23, 10, tzinfo=tz)) and not t.transfer_open(datetime(2026, 11, 23, 20, tzinfo=tz))
    t.transfer.only_during_hours = False
    assert t.transfer_open(datetime(2026, 11, 23, 20, tzinfo=tz))
    t.transfer.number = ""
    assert not t.transfer_open(datetime(2026, 11, 23, 10, tzinfo=tz))


def test_bad_values_are_rejected_or_defaulted():
    with pytest.raises(ValueError):
        TenantConfig.from_doc(_doc(slug="Not A Slug"))
    with pytest.raises(ValueError):
        HoursEntry(dow=1, open="9am", close="17:00")
    assert TenantConfig.from_doc(_doc(vertical="spaceport")).vertical == "generic"
    t = TenantConfig.from_doc(_doc(agent={"custom_instructions": "x" * 5000}))
    assert len(t.agent.custom_instructions) == 1500


def test_legacy_hotel_tenant_mirrors_settings():
    t = legacy_hotel_tenant()
    assert t.id == "default" and t.vertical == "hotel" and t.name == "Grand Azure Hotel"
    assert t.timezone == "Asia/Dhaka" and t.agent.name == "Aria" and len(t.hours) == 7


# ── Resolver ──────────────────────────────────────────────

async def test_legacy_mode_until_a_tenant_exists(db):
    assert (await resolve_by_phone_number("+15550001111")).id == "default"
    assert (await resolve_by_whatsapp_phone_number_id("anything")).id == "default"
    assert (await resolve_by_id("default")).id == "default"
    await db.tenants.insert_one(_doc())
    invalidate()
    assert await resolve_by_phone_number("+15550001111") is None          # unknown number now → None
    assert (await resolve_by_id("ten_a")).name == "Bella Dental"
    assert await resolve_by_id("default") is None


async def test_phone_slug_and_whatsapp_lookups(db):
    await db.tenants.insert_one(_doc())
    await db.phone_numbers.insert_one({"e164": "+15125551234", "tenant_id": "ten_a", "status": "active"})
    await db.phone_numbers.insert_one({"e164": "+15125559999", "tenant_id": "ten_a", "status": "released"})
    assert (await resolve_by_phone_number("+1 (512) 555-1234")).id == "ten_a"   # formatting ignored
    assert await resolve_by_phone_number("+15125559999") is None                # released numbers don't route
    assert (await resolve_by_slug("BELLA")).id == "ten_a"
    assert (await resolve_by_whatsapp_phone_number_id("pnid-ten_a")).id == "ten_a"
    assert await resolve_by_whatsapp_phone_number_id("pnid-nope") is None


async def test_cache_serves_for_ttl_and_invalidate_busts_it(db, monkeypatch):
    await db.tenants.insert_one(_doc())
    await db.phone_numbers.insert_one({"e164": "+15125551234", "tenant_id": "ten_a", "status": "active"})
    first = await resolve_by_phone_number("+15125551234")
    await db.tenants.update_one({"_id": "ten_a"}, {"$set": {"name": "Renamed"}})
    assert (await resolve_by_phone_number("+15125551234")).name == first.name == "Bella Dental"   # cached
    invalidate("ten_a")
    assert (await resolve_by_phone_number("+15125551234")).name == "Renamed"
    clock = {"t": 1000.0}
    monkeypatch.setattr(resolver, "_now", lambda: clock["t"])
    invalidate()
    await resolve_by_id("ten_a")
    await db.tenants.update_one({"_id": "ten_a"}, {"$set": {"name": "Again"}})
    clock["t"] += resolver.CACHE_TTL_S + 1
    assert (await resolve_by_id("ten_a")).name == "Again"


async def test_invalid_tenant_document_resolves_to_none_not_a_crash(db):
    await db.tenants.insert_one({"_id": "ten_bad", "slug": "bad slug!"})    # no name, bad slug
    assert await resolve_by_id("ten_bad") is None


# ── Repositories ──────────────────────────────────────────

async def test_tenant_repo_scopes_every_operation(db):
    a, b = Repos(db, "ten_a"), Repos(db, "ten_b")
    await a.contacts.insert_one({"_id": "c1", "phone": "+1555", "name": "Alice"})
    await b.contacts.insert_one({"_id": "c2", "phone": "+1555", "name": "Bob"})     # same phone, other tenant
    assert (await a.contacts.find_one({"phone": "+1555"}))["name"] == "Alice"
    assert (await b.contacts.find_one({"phone": "+1555"}))["name"] == "Bob"
    assert await a.contacts.find_one({"_id": "c2"}) is None                        # by id, still invisible
    assert await a.contacts.count() == 1
    # A query that smuggles in another tenant_id is overridden.
    assert await a.contacts.find_one({"tenant_id": "ten_b", "_id": "c2"}) is None
    # Updates and deletes can't cross tenants either.
    assert (await a.contacts.update_one({"_id": "c2"}, {"$set": {"name": "Hacked"}})).matched_count == 0
    assert (await a.contacts.update_one({"_id": "c1"}, {"$set": {"tenant_id": "ten_b", "name": "Alice A"}})).matched_count == 1
    assert (await b.contacts.find_one({"_id": "c1"})) is None
    assert (await a.contacts.delete_one({"_id": "c2"})).deleted_count == 0
    assert await b.contacts.count() == 1
    # Aggregations are pre-matched.
    rows = await b.contacts.aggregate([{"$group": {"_id": None, "n": {"$sum": 1}}}])
    assert rows[0]["n"] == 1


async def test_upsert_by_phone_creates_once(db):
    a = Repos(db, "ten_a")
    c1 = await a.contacts.upsert_by_phone("+1555", {"name": "Alice"})
    c2 = await a.contacts.upsert_by_phone("+1555", {"name": None, "email": "a@x.io"})
    assert c1["_id"] == c2["_id"] and c2["name"] == "Alice" and c2["email"] == "a@x.io"
    assert c2["tenant_id"] == "ten_a" and await a.contacts.count() == 1


def test_repo_requires_tenant_id(db):
    class R(TenantRepo):
        collection = "contacts"

    with pytest.raises(ValueError):
        R(db, "")


# ── Dental vertical ───────────────────────────────────────

def test_dental_vertical_and_old_documents_load_with_defaults():
    t = TenantConfig.from_doc(_doc(vertical="dental"))
    assert t.vertical == "dental"
    assert t.dental.accepts_nhs is False and t.dental.accepts_private_health_funds is False
    assert t.agent.stt_keyterms == [] and t.agent.recording.announce_text == ""
    old = TenantConfig.from_doc(_doc())                       # a pre-dental document: nothing new in it
    assert old.vertical == "clinic" and old.dental.finance_available is False


def test_split_shift_hours_close_for_lunch():
    hours = [HoursEntry(dow=0, open="09:00", close="13:00"), HoursEntry(dow=0, open="14:00", close="17:30")]
    t = TenantConfig.from_doc(_doc(vertical="dental", hours=[h.model_dump() for h in hours]))
    tz = ZoneInfo("America/Chicago")
    monday = lambda h, m: datetime(2026, 11, 23, h, m, tzinfo=tz)   # noqa: E731
    assert t.is_open(monday(12, 50)) and not t.is_open(monday(13, 30))
    assert t.is_open(monday(14, 15)) and not t.is_open(monday(17, 45))


def test_stt_keyterms_and_recording_notice_are_cleaned_and_capped():
    t = TenantConfig.from_doc(_doc(agent={"stt_keyterms": ["  Invisalign ", "invisalign", "", "x" * 80] +
                                                          [f"term{i}" for i in range(80)],
                                          "recording": {"enabled": True, "announce_text": "  Calls are recorded  for training. "}}))
    terms = t.agent.stt_keyterms
    assert terms[0] == "Invisalign" and terms.count("Invisalign") == 1 and len(terms) == 50
    assert all(len(w) <= 40 for w in terms)
    assert t.agent.recording.announce_text == "Calls are recorded for training."
    assert "Calls are recorded for training." in t.greeting_text()
    plain = TenantConfig.from_doc(_doc(agent={"recording": {"enabled": True}}))
    assert "This call may be recorded." in plain.greeting_text()


async def test_dental_tenants_start_with_lunch_break_hours_and_dental_services(db):
    from core.services.tenant_admin import create_tenant

    t = await create_tenant(name="Harbour Smiles", vertical="dental", timezone="Australia/Sydney", country="AU",
                            currency="AUD", tenant_id="ten_dent")
    monday = [(h.open, h.close) for h in t.hours_for(0)]
    assert monday == [("09:00", "13:00"), ("14:00", "17:30")] and not t.hours_for(5)
    services = await Repos(db, t.id).services.find({})
    assert {s["category"] for s in services} >= {"implant", "cosmetic", "emergency", "routine"}
