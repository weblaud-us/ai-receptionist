"""
Published legal documents and who agreed to them (Roadmap §11.8).

The texts are edited in the platform dashboard (Legal documents) and published on the marketing site
at /legal/<key> (core/compliance/legal_docs.py). Each has a version: the date its text last changed
in a way customers must accept. Publishing a material change there gives it a new version — owners
who accepted an older version see a "review the updated terms" notice (GET /t/legal), and the next
acceptance is recorded with the new version. The versions below are only the starting point, used
until a document is published from the platform.

Acceptances are evidence, so they are append-only lists on the user (terms, privacy, AUP) and
fields on the tenant (DPA — the business is the controller, so the DPA binds the business).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import settings

# key → (title, version, settings attribute holding the public URL)
DOCUMENTS: Dict[str, Dict[str, str]] = {
    "terms":         {"title": "Terms of Service",          "version": "2026-11-19", "url_setting": "TERMS_URL"},
    "privacy":       {"title": "Privacy Policy",            "version": "2026-11-20", "url_setting": "PRIVACY_URL"},
    "aup":           {"title": "Acceptable Use Policy",     "version": "2026-11-19", "url_setting": "AUP_URL"},
    "dpa":           {"title": "Data Processing Agreement", "version": "2026-11-20", "url_setting": "DPA_URL"},
    "subprocessors": {"title": "Sub-processors",            "version": "2026-11-20", "url_setting": "SUBPROCESSORS_URL"},
    "sla":           {"title": "Service Level Agreement",   "version": "2026-11-19", "url_setting": "SLA_URL"},
    "security":      {"title": "Security overview",         "version": "2026-11-19", "url_setting": "SECURITY_URL"},
    "subscription":  {"title": "Subscription and billing policy", "version": "2026-11-24", "url_setting": "SUBSCRIPTION_URL"},
    "cookies":       {"title": "Cookie policy",             "version": "2026-11-24", "url_setting": "COOKIES_URL"},
}
# What a person accepts by signing up (the checkbox on /signup).
SIGNUP_DOCUMENTS = ("terms", "privacy", "aup")

# The sub-processor list (§11.8). Keep in step with docs/legal/SUBPROCESSORS.md; customers get 30 days'
# notice of additions (DPA clause). `when` says when a vendor is actually used.
SUBPROCESSORS: List[Dict[str, str]] = [
    {"name": "Twilio Inc.", "purpose": "Phone numbers, call routing (SIP), SMS", "data": "Phone numbers, call audio in transit, SMS content",
     "location": "United States", "when": "always"},
    {"name": "LiveKit, Inc.", "purpose": "Real-time call media and SIP bridging", "data": "Call audio in transit, call metadata",
     "location": "United States / EU (by region)", "when": "always"},
    {"name": "Deepgram, Inc.", "purpose": "Speech-to-text (opted out of model improvement)", "data": "Caller audio",
     "location": "United States", "when": "always"},
    {"name": "OpenAI, L.L.C.", "purpose": "Language model; call summaries (storage off)", "data": "Transcripts, business information",
     "location": "United States", "when": "always"},
    {"name": "Anthropic, PBC", "purpose": "Fallback language model", "data": "Transcripts, business information",
     "location": "United States", "when": "if the primary language model fails"},
    {"name": "ElevenLabs Inc.", "purpose": "Text-to-speech", "data": "Assistant replies (text)",
     "location": "United States", "when": "when the business picks an ElevenLabs voice"},
    {"name": "Cartesia AI, Inc.", "purpose": "Text-to-speech", "data": "Assistant replies (text)",
     "location": "United States", "when": "by default (the platform's main voice), unless the business picks another"},
    {"name": "MongoDB, Inc. (Atlas)", "purpose": "Database hosting", "data": "All account and call data",
     "location": "United States / EU (by region)", "when": "always"},
    {"name": "Redis host", "purpose": "Short-lived state (rate limits, call slots, job queue)", "data": "Identifiers, no transcripts",
     "location": "Same region as the database", "when": "always"},
    {"name": "Cloud host", "purpose": "Application servers", "data": "All data in processing",
     "location": "Same region as the database", "when": "always"},
    {"name": "Stripe, Inc.", "purpose": "Subscriptions and payments", "data": "Business billing contact and payment details",
     "location": "United States", "when": "self-serve plans"},
    {"name": "Functional Software, Inc. (Sentry)", "purpose": "Error tracking", "data": "Technical identifiers; no transcripts",
     "location": "United States", "when": "always"},
    {"name": "Resend (Plus Five Five, Inc.)", "purpose": "Transactional e-mail", "data": "Owner e-mail address, notification content",
     "location": "United States", "when": "always"},
    {"name": "Google LLC", "purpose": "Calendar sync", "data": "Appointment times and titles",
     "location": "United States", "when": "only if the business connects Google Calendar"},
    {"name": "Meta Platforms, Inc. (WhatsApp)", "purpose": "WhatsApp messaging", "data": "Phone numbers, message content",
     "location": "United States", "when": "only if the business connects WhatsApp"},
    {"name": "Red Guava Pty Ltd (Cliniko)", "purpose": "The dental practice's own diary (bookings)",
     "data": "Patient name and phone number, appointment times and notes",
     "location": "The practice's Cliniko region (e.g. Australia)", "when": "only if the practice connects Cliniko"},
    {"name": "Dentally (Henry Schein One)", "purpose": "The dental practice's own diary (bookings)",
     "data": "Patient name and phone number, appointment times and notes",
     "location": "United Kingdom", "when": "only if the practice connects Dentally"},
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


Versions = Dict[str, str]


def url_for(key: str) -> str:
    """Where the document is published: its own *_URL setting, else the marketing site's /legal/<key>."""
    doc = DOCUMENTS.get(key)
    if not doc:
        return ""
    explicit = str(getattr(settings, doc["url_setting"], "") or "")
    if explicit:
        return explicit
    site = (settings.LEGAL_SITE_URL or "").rstrip("/")
    return f"{site}/legal/{key}" if site else ""


def version_of(key: str, versions: Optional[Versions] = None) -> str:
    """The current version: from `versions` (legal_docs.current_versions), else the built-in one."""
    return (versions or {}).get(key) or DOCUMENTS[key]["version"]


async def load_versions(db: Any) -> Versions:
    from core.compliance.legal_docs import current_versions

    return await current_versions(db)


def public_documents(versions: Optional[Versions] = None, published: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """`published`: keys with a published text (None = unknown, every document is listed as published)."""
    return [{"key": k, "title": d["title"], "version": version_of(k, versions), "url": url_for(k),
             "published": published is None or k in published} for k, d in DOCUMENTS.items()]


def acceptance_rows(keys: tuple[str, ...] | list[str], *, ip: str = "", user_agent: str = "",
                    now: Optional[datetime] = None, versions: Optional[Versions] = None) -> List[Dict[str, Any]]:
    """One evidence row per document: which version, when, from where."""
    now = now or utcnow()
    return [{"doc": k, "version": version_of(k, versions), "at": now, "ip": ip[:64], "user_agent": user_agent[:200]}
            for k in keys if k in DOCUMENTS]


def latest_accepted(rows: List[Dict[str, Any]] | None, key: str) -> Optional[str]:
    versions = [r.get("version") or "" for r in rows or [] if r.get("doc") == key]
    return max(versions) if versions else None


def outstanding(user_doc: Dict[str, Any], keys: tuple[str, ...] = SIGNUP_DOCUMENTS,
                versions: Optional[Versions] = None) -> List[str]:
    """Documents whose current version this user hasn't accepted (ISO dates compare as strings)."""
    rows = user_doc.get("legal_acceptances") or []
    return [k for k in keys if (latest_accepted(rows, k) or "") < version_of(k, versions)]


async def record_user_acceptance(db: Any, user_id: str, keys: tuple[str, ...] | list[str], *, ip: str = "",
                                 user_agent: str = "") -> List[Dict[str, Any]]:
    rows = acceptance_rows(keys, ip=ip, user_agent=user_agent, versions=await load_versions(db))
    if rows:
        await db.users.update_one({"_id": user_id}, {"$push": {"legal_acceptances": {"$each": rows}}})
    return rows


def dpa_status(tenant: Any, versions: Optional[Versions] = None) -> Dict[str, Any]:
    legal = tenant.legal
    current = version_of("dpa", versions)
    return {"version": current, "accepted_version": legal.dpa_version or None,
            "accepted_at": legal.dpa_accepted_at, "accepted_by": legal.dpa_accepted_by or None,
            "current": legal.dpa_version == current, "url": url_for("dpa")}
