"""
Transcript review and the eval suite built from real calls (Roadmap Task 7.1).

"Review every transcript yourself in week 1–2, then a sample. Each failure becomes a prompt fix, a
tool fix, a knowledge-base entry, and a new eval test case."

    review_queue(...)    calls waiting for review: every call of a pilot business, a deterministic
                         REVIEW_SAMPLE_PCT sample of everyone else
    save_review(...)     verdict + issue categories + notes, one review per call (`call_reviews`)
    case_from_call(...)  a redacted, replayable eval case (`eval_cases`, status draft → approved);
                         scripts/export_eval_cases.py writes approved ones to tests/evals/cases/,
                         which tests/evals/test_case_evals.py replays against the real LLM

Redaction: phone numbers, e-mail addresses, card-like numbers and street numbers are replaced, the
caller's name (from the contact) becomes "Alex". The business's own facts stay — they are what the
case tests. Review the draft before approving it; redaction is best effort, not a guarantee.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional, Sequence

from config import settings
from core.repositories import Repos
from core.tenancy.ids import new_id
from core.tenancy.models import TenantConfig

REVIEWS = "call_reviews"
CASES = "eval_cases"
ISSUES = ("prompt", "tool", "knowledge", "latency", "speech", "transfer", "hallucination", "other")
Verdict = Literal["good", "issue"]
MAX_CASE_TURNS = 12

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{6,}\d")
_LONG_DIGITS = re.compile(r"\b\d{5,}\b")
_STREET = re.compile(r"\b\d{1,5}(?=\s+[A-Z][a-z]+\s+(Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Dr|Boulevard|Blvd|Way|Court|Ct)\b)")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def in_sample(call_id: str, pct: int) -> bool:
    """Deterministic: the same call is always in or out, on every machine."""
    if pct >= 100:
        return True
    if pct <= 0:
        return False
    return int(hashlib.sha256(call_id.encode()).hexdigest()[:8], 16) % 100 < pct


def redact(text: str, *, names: Sequence[str] = ()) -> str:
    text = _EMAIL.sub("alex@example.com", text or "")
    text = _PHONE.sub("+1 555 0100", text)
    text = _LONG_DIGITS.sub("00000", text)
    text = _STREET.sub("100", text)
    for name in names:
        for part in {name, *name.split()}:
            if len(part) >= 3:
                text = re.sub(rf"\b{re.escape(part)}\b", "Alex", text, flags=re.IGNORECASE)
    return text


async def review_queue(db: Any, *, tenant_id: Optional[str] = None, days: int = 14, limit: int = 50,
                       now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    now = now or utcnow()
    tenants = {t["_id"]: t for t in await db.tenants.find({}, {"name": 1, "pilot": 1}).to_list(10_000)}
    q: Dict[str, Any] = {"status": "completed", "direction": {"$ne": "outbound"}, "started_at": {"$gte": now - timedelta(days=days)},
                         "$or": [{"transcript.0": {"$exists": True}}, {"transcript_enc": {"$exists": True}}]}
    if tenant_id:
        q["tenant_id"] = tenant_id
    reviewed = {r["call_id"] for r in await db[REVIEWS].find({}, {"call_id": 1}).to_list(100_000)}
    out: List[Dict[str, Any]] = []
    cursor = db.calls.find(q, {"call_id": 1, "tenant_id": 1, "started_at": 1, "duration_s": 1, "outcome": 1,  # cross-tenant: the platform's transcript-review sample
                               "summary": 1, "latency": 1, "tools_used": 1, "after_hours": 1}).sort("started_at", -1)
    async for call in cursor:
        if call["call_id"] in reviewed:
            continue
        t = tenants.get(call.get("tenant_id")) or {}
        pilot = bool((t.get("pilot") or {}).get("active"))
        if not pilot and not tenant_id and not in_sample(call["call_id"], settings.REVIEW_SAMPLE_PCT):
            continue
        call.pop("_id", None)
        out.append({**call, "business": t.get("name", ""), "pilot": pilot})
        if len(out) >= limit:
            break
    return out


async def save_review(db: Any, *, tenant_id: str, call_id: str, verdict: Verdict, issues: List[str], notes: str,
                      reviewer: str, expected: str = "") -> Dict[str, Any]:
    call = await db.calls.find_one({"tenant_id": tenant_id, "call_id": call_id}, {"_id": 1})  # scoped: tenant_id
    if call is None:
        raise LookupError("call not found")
    doc = {"tenant_id": tenant_id, "call_id": call_id, "verdict": verdict,
           "issues": [i for i in dict.fromkeys(issues or []) if i in ISSUES], "notes": (notes or "")[:2000],
           "expected": (expected or "")[:1000], "reviewer": reviewer[:80], "at": utcnow()}
    await db[REVIEWS].update_one({"call_id": call_id}, {"$set": doc, "$setOnInsert": {"_id": new_id("rev")}},
                                 upsert=True)
    return doc


def _tenant_facts(t: TenantConfig, services: List[Dict[str, Any]], knowledge: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "name": t.name, "vertical": t.vertical, "timezone": t.timezone, "country": t.country,
        "currency": t.currency, "description": t.description,
        "hours": [h.model_dump() for h in t.hours],
        "agent": {"name": t.agent.name, "tone": t.agent.tone},
        "services": [{k: s.get(k) for k in ("name", "duration_min", "price", "description") if s.get(k) is not None}
                     for s in services[:20]],
        "knowledge": [{"question": k.get("question"), "answer": k.get("answer")} for k in knowledge[:30]
                      if k.get("question") and k.get("answer")],
    }


async def case_from_call(db: Any, tenant: TenantConfig, call_id: str, *, title: str, expected: str,
                         must_call: Sequence[str] = (), must_not_call: Sequence[str] = (), author: str = "") -> Dict[str, Any]:
    call = await db.calls.find_one({"tenant_id": tenant.id, "call_id": call_id})  # scoped: tenant_id
    if call is not None:
        from core.sensitive import unseal

        call = unseal(call)
    if call is None or not call.get("transcript"):
        raise LookupError("call not found or its transcript has been purged")
    repos = Repos(db, tenant.id)
    contact = await repos.contacts.find_one({"phone": call.get("caller_phone")}, {"name": 1}) \
        if call.get("caller_phone") else None
    names = [contact["name"]] if contact and contact.get("name") else []
    turns = [redact(t.get("text") or "", names=names) for t in call["transcript"] if t.get("role") == "user"]
    turns = [t for t in turns if t.strip()][:MAX_CASE_TURNS]
    if not turns:
        raise ValueError("the caller said nothing that could be replayed")
    services = await repos.services.find({"active": {"$ne": False}}, limit=50)
    knowledge = await repos.knowledge.find({"active": {"$ne": False}}, limit=50)
    case = {
        "_id": new_id("evc"), "tenant_id": tenant.id, "source_call_id": call_id, "status": "draft",
        "title": title[:120], "vertical": tenant.vertical,
        "tenant": _tenant_facts(tenant, services, knowledge),
        "caller_turns": turns,
        "expect": {"intent": expected[:1000], "must_call": [x for x in must_call if x][:5],
                   "must_not_call": [x for x in must_not_call if x][:5]},
        "author": author[:80], "created_at": utcnow(),
    }
    await db[CASES].insert_one(case)
    return case


def case_file(case: Dict[str, Any]) -> Dict[str, Any]:
    """The JSON written to tests/evals/cases/ — no tenant id, no call id, nothing to trace back."""
    return {"id": case["_id"], "title": case["title"], "vertical": case["vertical"], "tenant": case["tenant"],
            "caller_turns": case["caller_turns"], "expect": case["expect"]}


async def stats(db: Any, *, days: int = 30, now: Optional[datetime] = None) -> Dict[str, Any]:
    since = (now or utcnow()) - timedelta(days=days)
    by_tenant: Dict[str, Dict[str, Any]] = {}
    issues: Dict[str, int] = {}
    async for r in db[REVIEWS].find({"at": {"$gte": since}}):
        row = by_tenant.setdefault(r["tenant_id"], {"reviewed": 0, "issues": 0})
        row["reviewed"] += 1
        if r.get("verdict") == "issue":
            row["issues"] += 1
        for i in r.get("issues") or []:
            issues[i] = issues.get(i, 0) + 1
    return {"days": days, "by_tenant": by_tenant, "issues": issues,
            "eval_cases": {"draft": await db[CASES].count_documents({"status": "draft"}),
                           "approved": await db[CASES].count_documents({"status": "approved"})}}
