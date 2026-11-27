"""
The legal documents' text: edited by platform admins (Platform → Legal documents), published on the
marketing site (landing-page /legal/<key>, read from GET /public/legal/documents/<key>).

Two collections:
- `legal_documents`, one per key (terms, privacy, subscription, cookies, aup, dpa, …): the working draft
  and the published text.
- `legal_document_versions`, append-only: every text ever published, with its version and who published
  it. Acceptances record a version (core/compliance/legal.py), so this is the evidence of what was agreed.

Publishing a *material* change gives the document a new version (the date, with .2, .3… if published
twice in a day; ISO strings still compare in order). Owners who accepted an older version then see
"review the updated terms" (GET /t/legal → outstanding). A non-material publish (a typo) keeps the version.
A document that still has [square-bracket] placeholders can't be published.

The first texts come from core/compliance/legal_seed/*.md (the drafts in docs/legal, plus the subscription
and cookie policies), with the server's own values filled in. Documents that existed before keep their
versions, so nobody is asked to accept again just because this was deployed.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import settings
from core.compliance.legal import DOCUMENTS

LEGAL_DOCS = "legal_documents"
LEGAL_VERSIONS = "legal_document_versions"
SEED_DIR = Path(__file__).resolve().parent / "legal_seed"
MAX_CHARS = 200_000

# A placeholder is text in square brackets that isn't a link's text ("[Privacy Policy](/legal/privacy)").
_PLACEHOLDER = re.compile(r"(?<!\\)\[([^\[\]\n]{0,200})\](?!\()")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Rendering ────────────────────────────────────────────────────────────────

def render(md: str) -> str:
    """Markdown → HTML. Raw HTML in the text is escaped, never passed through, and links with unsafe
    schemes (javascript:, data:…) are refused by the parser, so the result is safe to show as-is."""
    from markdown_it import MarkdownIt

    parser = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False}).enable("table")
    return parser.render(md or "")


def placeholders(md: str) -> List[str]:
    """The [bracketed] bits still to fill in, in order, without repeats."""
    seen: List[str] = []
    for m in _PLACEHOLDER.finditer(md or ""):
        text = m.group(0)
        if text not in seen:
            seen.append(text)
    return seen


# ── Seed texts ───────────────────────────────────────────────────────────────

def _plan_rows() -> str:
    from core.billing.plans import PLANS

    rows = []
    for key in ("dental_essentials", "dental_pro"):
        plan = PLANS.get(key)
        if not plan:
            continue
        rows.append(f"| {plan.name} | A${plan.monthly_in('AUD'):,} | £{plan.monthly_in('GBP'):,} | "
                    f"{plan.included_minutes:,} | A${plan.setup_fee_in('AUD'):,} / £{plan.setup_fee_in('GBP'):,} |")
    return "\n".join(rows)


def _tokens() -> Dict[str, str]:
    from core.billing.plans import ANNUAL_MONTHS_CHARGED, OVERAGE_PER_MIN

    privacy = settings.PRIVACY_CONTACT_EMAIL or settings.SUPPORT_EMAIL
    return {
        "LEGAL_ENTITY_NAME": settings.LEGAL_ENTITY_NAME,
        "TRIAL_DAYS": str(settings.TRIAL_DAYS),
        "TRIAL_DAYS_PLUS_ONE": str(settings.TRIAL_DAYS + 1),
        "TRIAL_MINUTES": str(settings.TRIAL_MINUTES),
        "CANCEL_RETENTION_DAYS": str(settings.CANCEL_RETENTION_DAYS),
        "PAST_DUE_SUSPEND_DAYS": str(settings.PAST_DUE_SUSPEND_DAYS),
        "WEB_DEMO_TRANSCRIPT_DAYS": str(settings.WEB_DEMO_TRANSCRIPT_DAYS),
        "WEB_DEMO_LEAD_RETENTION_DAYS": str(settings.WEB_DEMO_LEAD_RETENTION_DAYS),
        "ANNUAL_MONTHS": str(ANNUAL_MONTHS_CHARGED),
        "OVERAGE": f"A${OVERAGE_PER_MIN['AUD']:.2f} (Australia) or £{OVERAGE_PER_MIN['GBP']:.2f} (United Kingdom)",
        "PLAN_ROWS": _plan_rows(),
        "SUPPORT_EMAIL": settings.SUPPORT_EMAIL or "[support e-mail address]",
        "PRIVACY_CONTACT": (f"Questions about cookies or privacy: {privacy}." if privacy
                            else "[privacy contact e-mail address]"),
    }


def seed_text(key: str) -> str:
    """The first text of a document: its seed file, tidied for publishing, with known values filled in."""
    path = SEED_DIR / f"{key}.md"
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    out: List[str] = []
    preamble = True
    for line in lines:
        if preamble:
            # The page shows the title and the publication date itself; the drafting notes are for us.
            if line.startswith("# ") or line.startswith(">") or not line.strip():
                continue
            if re.match(r"\*\*Last (updated|reviewed):\*\*", line):
                continue
            preamble = False
        out.append(line)
    md = "\n".join(out).strip() + "\n"

    # Links between the drafts → the published pages.
    def link(m: re.Match) -> str:
        k = m.group(2).lower()
        return f"[{m.group(1) if not m.group(1).endswith('.md') else DOCUMENTS.get(k, {}).get('title', k)}](/legal/{k})"
    md = re.sub(r"\[([^\]\n]+)\]\(([A-Z]+)\.md\)", link, md)
    md = re.sub(r"\(\.\./COMPLIANCE\.md\)", "(/legal/privacy)", md)

    # Values the server knows.
    for name, value in _tokens().items():
        md = md.replace("{{" + name + "}}", value)
    md = md.replace("[14] days or [50] minutes", f"{settings.TRIAL_DAYS} days or {settings.TRIAL_MINUTES} minutes")
    for name in ("PRIVACY_CONTACT_EMAIL", "SUPPORT_EMAIL", "STATUS_PAGE_URL"):
        value = getattr(settings, name, "") or ""
        if value:
            md = md.replace(f"[{name}]", value)
    md = re.sub(r"\[(\d+(?:\.\d+)?)\]", r"\1", md)          # a number the draft suggested: keep it
    return md


async def ensure_seeded(db: Any) -> None:
    """Create any document that doesn't exist yet. Safe to run on every replica at once."""
    now = utcnow()
    for key, meta in DOCUMENTS.items():
        if await db[LEGAL_DOCS].find_one({"_id": key}, {"_id": 1}):
            continue
        md = seed_text(key)
        publishable = bool(md.strip()) and not placeholders(md)
        published = ({"version": meta["version"], "md": md, "html": render(md), "published_at": now,
                      "by": "seed", "summary": "First published text"} if publishable else None)
        res = await db[LEGAL_DOCS].update_one({"_id": key}, {"$setOnInsert": {
            "title": meta["title"], "draft_md": md, "draft_updated_at": now, "draft_updated_by": "seed",
            "published": published, "created_at": now}}, upsert=True)
        if published and res.upserted_id is not None:
            await db[LEGAL_VERSIONS].insert_one({"key": key, "version": meta["version"], "revision": 1, "md": md,
                                           "html": published["html"], "published_at": now, "by": "seed",
                                           "material": True, "summary": published["summary"]})


async def ensure_ready(db: Any) -> None:
    await db[LEGAL_VERSIONS].create_index([("key", 1), ("published_at", -1)])
    await ensure_seeded(db)


# ── Reading ──────────────────────────────────────────────────────────────────

async def current_versions(db: Any) -> Dict[str, str]:
    """Each document's version customers must have accepted: the published one, else the built-in one."""
    out = {k: d["version"] for k, d in DOCUMENTS.items()}
    async for d in db[LEGAL_DOCS].find({"published": {"$ne": None}}, {"published.version": 1}):
        v = (d.get("published") or {}).get("version")
        if v and d["_id"] in out:
            out[d["_id"]] = v
    return out


async def published_keys(db: Any) -> List[str]:
    return [d["_id"] async for d in db[LEGAL_DOCS].find({"published": {"$ne": None}}, {"_id": 1})]


def _summary(doc: Dict[str, Any]) -> Dict[str, Any]:
    pub = doc.get("published") or None
    draft = doc.get("draft_md") or ""
    return {
        "key": doc["_id"], "title": doc.get("title") or DOCUMENTS.get(doc["_id"], {}).get("title", doc["_id"]),
        "published_version": pub["version"] if pub else None,
        "published_at": pub["published_at"] if pub else None,
        "draft_updated_at": doc.get("draft_updated_at"),
        "unpublished_changes": (pub is None and bool(draft.strip())) or (pub is not None and pub.get("md") != draft),
        "placeholders": len(placeholders(draft)),
    }


async def list_documents(db: Any) -> List[Dict[str, Any]]:
    await ensure_seeded(db)
    docs = {d["_id"]: d async for d in db[LEGAL_DOCS].find({})}
    return [_summary(docs[k]) for k in DOCUMENTS if k in docs]


async def get_document(db: Any, key: str) -> Optional[Dict[str, Any]]:
    await ensure_seeded(db)
    doc = await db[LEGAL_DOCS].find_one({"_id": key})
    if not doc:
        return None
    history = await db[LEGAL_VERSIONS].find({"key": key}, {"md": 0, "html": 0}).sort("published_at", -1).to_list(100)
    pub = doc.get("published") or None
    return {**_summary(doc), "draft_md": doc.get("draft_md") or "",
            "draft_placeholders": placeholders(doc.get("draft_md") or ""),
            "published_md": pub["md"] if pub else "",
            "history": [{k: v for k, v in h.items() if k != "_id"} for h in history]}


async def public_document(db: Any, key: str) -> Optional[Dict[str, Any]]:
    await ensure_seeded(db)
    doc = await db[LEGAL_DOCS].find_one({"_id": key}, {"title": 1, "published": 1})
    pub = (doc or {}).get("published")
    if not pub:
        return None
    return {"key": key, "title": doc.get("title") or DOCUMENTS[key]["title"], "version": pub["version"],
            "published_at": pub["published_at"], "html": pub["html"]}


async def version_text(db: Any, key: str, version: str) -> Optional[Dict[str, Any]]:
    row = await db[LEGAL_VERSIONS].find_one({"key": key, "version": version}, sort=[("revision", -1)])
    return {k: v for k, v in row.items() if k != "_id"} if row else None


# ── Writing ──────────────────────────────────────────────────────────────────

class LegalDocError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


async def save_draft(db: Any, key: str, *, title: str, md: str, by: str) -> Dict[str, Any]:
    if key not in DOCUMENTS:
        raise LegalDocError("Unknown document", 404)
    if len(md) > MAX_CHARS:
        raise LegalDocError(f"That's too long (at most {MAX_CHARS:,} characters)")
    await ensure_seeded(db)
    await db[LEGAL_DOCS].update_one({"_id": key}, {"$set": {
        "title": title.strip() or DOCUMENTS[key]["title"], "draft_md": md, "draft_updated_at": utcnow(),
        "draft_updated_by": by}}, upsert=True)
    doc = await get_document(db, key)
    assert doc is not None                     # seeded and just upserted
    return doc


def next_version(today: date, previous: Optional[str]) -> str:
    """Today's date, or (if that isn't later than the last version) the last version plus one."""
    base = today.isoformat()
    if not previous or base > previous:
        return base
    head, _, n = previous.partition(".")
    return f"{head}.{int(n) + 1 if n.isdigit() else 2}"


async def publish(db: Any, key: str, *, material: bool, summary: str, by: str,
                  today: Optional[date] = None) -> Dict[str, Any]:
    await ensure_seeded(db)
    doc = await db[LEGAL_DOCS].find_one({"_id": key})
    if not doc:
        raise LegalDocError("Unknown document", 404)
    md = doc.get("draft_md") or ""
    if not md.strip():
        raise LegalDocError("There's no text to publish")
    left = placeholders(md)
    if left:
        shown = ", ".join(left[:5]) + ("…" if len(left) > 5 else "")
        raise LegalDocError(f"Fill in the {len(left)} bracketed placeholder{'s' if len(left) != 1 else ''} first: {shown}")
    prev = doc.get("published") or None
    prev_version = prev["version"] if prev else None
    if material or not prev_version:
        # The first publication of a document keeps the built-in version, so earlier acceptances still count.
        version = next_version(today or utcnow().date(), prev_version) if prev_version else DOCUMENTS[key]["version"]
    else:
        version = prev_version
    now = utcnow()
    html = render(md)
    last = await db[LEGAL_VERSIONS].find_one({"key": key, "version": version}, sort=[("revision", -1)])
    revision = (last or {}).get("revision", 0) + 1
    await db[LEGAL_VERSIONS].insert_one({"key": key, "version": version, "revision": revision, "md": md, "html": html,
                                   "published_at": now, "by": by, "material": bool(material or not prev_version),
                                   "summary": summary.strip()[:500]})
    await db[LEGAL_DOCS].update_one({"_id": key}, {"$set": {"published": {
        "version": version, "md": md, "html": html, "published_at": now, "by": by, "summary": summary.strip()[:500]}}})
    return {"key": key, "version": version, "revision": revision, "material": bool(material or not prev_version),
            "previous_version": prev_version}
