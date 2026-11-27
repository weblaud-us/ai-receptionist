"""
The playbook: what the agent has learned about running calls for one offer (SALES_LEARNING_PLAN.md
Phase 3).

A playbook is a short list of coaching entries — "when <situation>, <what to do>", with an optional
example question — that go into the prompt as a fenced <playbook> block right after the offer. It
lives outside the offer (`OfferSettings`), so it never changes an offer's text, checksum or version,
nor a campaign's legal review. What keeps it inside the approved offer is this module:

- **The validator** (`problems`) refuses any entry that brings in a figure, a price or terms, a claim,
  anything about being an AI or a person, recording, opting out or consent, SMS or WhatsApp, a link
  or contact detail, an instruction aimed at the model, opener wording, or a name the offer doesn't
  already use. The example line must be one short question.
- **Test calls**: a playbook is used on calls for an offer version only after it passed the full
  simulated test-call suite for that exact version text, playbook text and script (`eligible`).
- **Consent**: a campaign uses playbooks only when the person who signed its legal review also
  allowed learning, under the current LEARNING_POLICY_VERSION (`learning.assign`).
- Entries never change once written: an edit is a new playbook version. Pinning is the one thing a
  person can change in place, and it isn't part of the checksum.

Storage: `sales_playbooks` (one document per version, `_id` "{offer_id}@pb{n}") and `sales_learning`
(one per offer: the mode, the live playbook, the running test).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReturnDocument

from config import settings
from core.outbound import models as m
from core.outbound import offers as of

logger = logging.getLogger(__name__)

PLAYBOOKS = "sales_playbooks"
LEARNING = "sales_learning"
KINDS: tuple[str, ...] = ("gatekeeper", "bridge", "discovery", "pitch", "objection", "close", "tone", "avoid")
KIND_LABEL: Mapping[str, str] = {
    "gatekeeper": "With a gatekeeper", "bridge": "After the opener", "discovery": "Asking about them",
    "pitch": "Showing the value", "objection": "An objection", "close": "Asking for the meeting",
    "tone": "How to sound", "avoid": "Avoid",
}
STATUSES: tuple[str, ...] = ("awaiting_tests", "ready", "testing", "live", "retired", "rolled_back", "failed_checks")
MODES: tuple[str, ...] = ("autopilot", "paused", "off")
MAX_ENTRIES = 12
MAX_RENDERED = 2500
MAX_SAY_WORDS = 20
# What a campaign's sign-off agreed to when it allowed learning. Changing what the playbook may do
# (new kinds of entry, fewer checks) means a new version here, and every campaign asks again.
LEARNING_POLICY_VERSION = "2026-11-v1"

Kind = Literal["gatekeeper", "bridge", "discovery", "pitch", "objection", "close", "tone", "avoid"]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="ignore")
    leads: int = Field(default=0, ge=0)
    calls: int = Field(default=0, ge=0)
    progressed: int = Field(default=0, ge=0)
    attempt_ids: List[str] = Field(default_factory=list, max_length=5)


class Entry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(pattern=r"^[a-z0-9_]{2,40}$")
    kind: Kind
    when: str = Field(min_length=3, max_length=120)
    do: str = Field(min_length=5, max_length=240)
    say: str = Field(default="", max_length=160)
    origin: Literal["learned", "manual"] = "manual"
    pinned: bool = False
    evidence: Evidence = Field(default_factory=Evidence)
    since: int = Field(default=1, ge=1)


# ── The validator ────────────────────────────────────────────────────────────

# Price or terms. ("What a missed call costs them" or "their no-show rate" are fine: that's about them.)
_MONEY = re.compile(r"[$£€]|\b(?:price\w*|pricing|fees?|monthly|per month|a month|per year|a year|discount\w*|free|"
                    r"trial|pilot|contract\w*|refund\w*|deposit\w*|invoice\w*|cheap\w*|expensive)\b",
                    re.IGNORECASE)
_CLAIM = re.compile(r"\b(?:guarantee\w*|promis\w*|proven|on average|studies show|research shows|number one|"
                    r"best in|award\w*|other (?:practices|clinics|businesses|clients|customers|dentists)|"
                    r"most (?:practices|clinics|businesses|of our)|our clients|our customers|we've helped|we have helped)\b",
                    re.IGNORECASE)
# Who the agent is, and recording: the fixed rules own these. ("AI tools" or "an answering machine" are
# topics, not identity.)
_IDENTITY = re.compile(r"\b(?:i'?m|i am|you'?re|you are|we'?re|we are|being)\s+(?:an?\s+|not\s+an?\s+)?"
                       r"(?:ai|a\.i\.|artificial|robot|bot|machine|human|person|real)\b|"
                       r"\b(?:is|are)\s+(?:this|it|you)\s+(?:an?\s+)?(?:ai|robot|bot|machine|human|real)\b|"
                       r"\breal person\b|\bhuman being\b|\b(?:a|the) human\b|\brecord(?:ed|ing)\b", re.IGNORECASE)
_OPT_OUT = re.compile(r"\b(?:do not call|don'?t call|opt[- ]?out|unsubscrib\w*|off (?:the|our|your) list|"
                      r"consent\w*|permission to call|stop calling)\b", re.IGNORECASE)
_CHANNEL = re.compile(r"\b(?:sms|text message\w*|texting|text them|whatsapp)\b", re.IGNORECASE)
_CONTACT = re.compile(r"https?://|www\.|\.(?:com|net|org|au|uk|io)\b|@|\+?\d[\d\s-]{6,}\d", re.IGNORECASE)
# Aimed at the model. ("We already have a developer" is an objection, so "developer" is fine.)
_META = re.compile(r"\b(?:ignore (?:the|your|all|any|previous|prior|these)|disregard|instructions?|system prompt|"
                   r"system message|you are now|pretend|jailbreak|override|role ?play)\b", re.IGNORECASE)
_OPENER = re.compile(r"\b(?:this is \w+|calling from|on behalf of|my name is)\b", re.IGNORECASE)
_BIG_NUMBER = re.compile(r"\b(?:hundred|thousand|million|billion|dozen|percent|per cent)\b|%", re.IGNORECASE)
_DIGIT = re.compile(r"\d[\d,.]*")
_CAPITAL = re.compile(r"\b[A-Z][a-zA-Z'’-]+")
_PLACEHOLDER = re.compile(r"[{}]")
_WEEKDAYS_MONTHS = {d for d in ("Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February March "
                                "April May June July August September October November December I OK").split()}


def offer_text(offer: Mapping[str, Any]) -> str:
    """Everything the offer itself says: figures and names found here may appear in the playbook."""
    parts: List[str] = [str(offer.get(k) or "") for k in ("name", "company_name", "persona_name", "what_we_sell",
                                                           "ideal_customer")]
    for key in ("value_props", "proof_points", "discovery_questions", "qualification_questions", "pain_hypotheses"):
        parts += [str(x) for x in offer.get(key) or []]
    for o in offer.get("objections") or []:
        if isinstance(o, Mapping):
            parts += [str(o.get("objection") or ""), str(o.get("response") or "")]
    for v in offer.get("variants") or []:
        if isinstance(v, Mapping):
            parts += [str(v.get("first_question") or ""), str(v.get("pitch") or ""), str(v.get("label") or "")]
    meeting = offer.get("meeting") or {}
    parts += [str(meeting.get("label") or ""), str(meeting.get("host_name") or ""), f"{meeting.get('length_min') or ''}"]
    return " ".join(parts)


def _sentences(text: str) -> List[str]:
    return [s for s in re.split(r"(?<=[.!?;:])\s+|\s+[—–-]\s+|[\"“”‘’()]", text) if s.strip()]


def _names_outside(text: str, allowed: set[str]) -> List[str]:
    """Capitalised words that aren't a sentence's first word and aren't used by the offer itself."""
    out = []
    for sentence in _sentences(text):
        words = _CAPITAL.findall(sentence)
        first = sentence.strip().split()[0] if sentence.strip() else ""
        for w in words:
            if w == first.strip("\"'“”‘’(") or w in _WEEKDAYS_MONTHS or w.lower() in allowed:
                continue
            if w.isupper() and len(w) <= 4:            # CRM, ROI, PMS: acronyms, not names
                continue
            out.append(w)
    return out


def problems(e: Entry, offer: Mapping[str, Any]) -> List[str]:
    """Why this entry can't be used with this offer version. Empty = fine."""
    source = offer_text(offer)
    allowed_digits = {d.replace(",", "").rstrip(".") for d in _DIGIT.findall(source)}
    allowed_words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'’-]+", source)}
    text = " ".join(f"{e.when}. {e.do}. {e.say}".split())
    out = []
    if any(d.replace(",", "").rstrip(".") not in allowed_digits for d in _DIGIT.findall(text)):
        out.append("uses a figure the offer doesn't contain")
    if _BIG_NUMBER.search(text) and not all(w.lower() in allowed_words for w in _BIG_NUMBER.findall(text)):
        out.append("uses a figure the offer doesn't contain")
    for pattern, why in ((_MONEY, "talks about price or terms (only the offer's price sentence may)"),
                         (_CLAIM, "makes a claim (only the offer's proof points may)"),
                         (_IDENTITY, "touches who the agent is or recording (the fixed rules handle that)"),
                         (_OPT_OUT, "touches opting out or consent (the fixed rules handle that)"),
                         (_CHANNEL, "mentions SMS or WhatsApp"),
                         (_CONTACT, "contains a link or contact details"),
                         (_META, "reads like an instruction to the AI"),
                         (_OPENER, "reads like an opener (openers are fixed and reviewed)")):
        if pattern.search(text):
            out.append(why)
    if of.FENCE_TAG.search(text) or re.search(r"</?\s*[a-z_]+\s*>", text) or _PLACEHOLDER.search(text):
        out.append("contains tags or placeholders")
    names = _names_outside(text, allowed_words)
    if names:
        out.append(f"names someone or something the offer doesn't mention ({', '.join(sorted(set(names))[:3])})")
    if e.say and (not e.say.strip().endswith("?") or len(e.say.split()) > MAX_SAY_WORDS):
        out.append(f"the example line must be one short question (at most {MAX_SAY_WORDS} words)")
    return list(dict.fromkeys(out))


def render(entries: Sequence[Entry]) -> List[str]:
    """The prompt lines, grouped by kind, in a stable order (pinning never changes what is said)."""
    ordered = sorted(entries, key=lambda e: (KINDS.index(e.kind), e.id))
    lines = []
    for e in ordered:
        line = f"{KIND_LABEL[e.kind]}: when {e.when.rstrip('.')}, {e.do.rstrip('.')}."
        if e.say:
            line += f' For example: "{e.say}"'
        lines.append(line)
    return lines


def validate(entries: Sequence[Entry], offer: Mapping[str, Any]) -> Tuple[List[Entry], List[Dict[str, Any]]]:
    """(entries that may be used, [{entry, problems}] that may not). Also holds the size limits:
    pinned entries first, then the rest in order, until MAX_ENTRIES or MAX_RENDERED is reached."""
    kept: List[Entry] = []
    dropped: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for e in sorted(entries, key=lambda x: not x.pinned):
        issues = problems(e, offer)
        if e.id in seen:
            issues.append("the same id is used twice")
        if not issues and len(kept) >= MAX_ENTRIES:
            issues.append(f"the playbook already has {MAX_ENTRIES} entries")
        if not issues and sum(len(x) + 3 for x in render([*kept, e])) > MAX_RENDERED:
            issues.append("the playbook would be too long for a quick reply")
        if issues:
            dropped.append({"entry": e.model_dump(), "problems": issues})
        else:
            kept.append(e)
            seen.add(e.id)
    return kept, dropped


def checksum(entries: Sequence[Entry]) -> str:
    """What the agent is told, nothing else: pinning or evidence don't change it."""
    return of.checksum({"entries": [{"kind": e.kind, "when": e.when, "do": e.do, "say": e.say}
                                    for e in sorted(entries, key=lambda x: (KINDS.index(x.kind), x.id))]})


def entries_of(doc: Optional[Mapping[str, Any]]) -> List[Entry]:
    out = []
    for raw in (doc or {}).get("entries") or []:
        try:
            out.append(Entry.model_validate(raw))
        except Exception:
            continue
    return out


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Storage ──────────────────────────────────────────────────────────────────

def _default_learning(offer_id: str) -> Dict[str, Any]:
    return {"_id": offer_id, "mode": "autopilot", "live_playbook_id": None, "experiment_id": None,
            "share": settings.SALES_EXPERIMENT_SHARE, "pb_seq": 0, "last_learn_at": None, "last_learn_reviews": 0,
            "last_rollback_at": None}


async def learning_doc(db: Any, offer_id: str) -> Dict[str, Any]:
    """The offer's learning state (created with the defaults the first time it's asked for)."""
    doc = await db[LEARNING].find_one({"_id": offer_id})
    if doc:
        return {**_default_learning(offer_id), **doc}
    try:
        await db[LEARNING].insert_one(_default_learning(offer_id))
    except Exception:                                 # another tick created it first
        pass
    return {**_default_learning(offer_id), **(await db[LEARNING].find_one({"_id": offer_id}) or {})}


async def create(db: Any, offer_id: str, entries: Sequence[Entry], *, parent: Optional[str], changes: Sequence[Mapping],
                 origin: str, by: str, now: Optional[datetime] = None, go_live: bool = False,
                 why: str = "") -> Dict[str, Any]:
    """A new playbook version, waiting for its test calls. Its number comes from the offer's own counter."""
    now = now or utcnow()
    await learning_doc(db, offer_id)
    seq = await db[LEARNING].find_one_and_update({"_id": offer_id}, {"$inc": {"pb_seq": 1}},
                                                 return_document=ReturnDocument.AFTER)
    n = int(seq["pb_seq"])
    stamped = [e.model_copy(update={"since": e.since if e.since and e.since < n else n}) for e in entries]
    doc = {"_id": f"{offer_id}@pb{n}", "offer_id": offer_id, "n": n, "status": "awaiting_tests",
           "entries": [e.model_dump() for e in stamped], "checksum": checksum(stamped), "parent": parent,
           "changes": [dict(c) for c in changes], "origin": origin, "created_by": by, "created_at": now,
           "why": why[:300], "go_live": bool(go_live), "targets": [], "decision": None, "test_retries": 0}
    await db[PLAYBOOKS].insert_one(doc)
    return doc


async def get(db: Any, playbook_id: Optional[str]) -> Optional[Dict[str, Any]]:
    return await db[PLAYBOOKS].find_one({"_id": playbook_id}) if playbook_id else None


async def version_doc(db: Any, offer_id: str, version: int) -> Optional[Dict[str, Any]]:
    return await db[of.OFFER_VERSIONS].find_one({"_id": f"{offer_id}@{int(version)}"})


async def targets(db: Any, offer_id: str) -> List[Dict[str, Any]]:
    """The offer versions and scripts a playbook must pass its tests with: those that running or
    paused campaigns use, or (with none) the latest published version and all its scripts."""
    campaigns = await db[m.CAMPAIGNS].find({"offer_id": offer_id, "status": {"$in": ["running", "paused"]},
                                            "mode": {"$ne": "human_assisted"}},
                                           {"offer_version": 1, "variant_ids": 1}).to_list(200)
    wanted: Dict[int, set[str]] = {}
    for c in campaigns:
        if c.get("offer_version"):
            wanted.setdefault(int(c["offer_version"]), set()).update(c.get("variant_ids") or [])
    if not wanted:
        offer = await db[of.OFFERS].find_one({"_id": offer_id}, {"published_version": 1})
        if offer and offer.get("published_version"):
            wanted[int(offer["published_version"])] = set()
    out = []
    for version, variants in sorted(wanted.items()):
        snap = await version_doc(db, offer_id, version)
        if not snap:
            continue
        ids = sorted(variants) or [v["id"] for v in (snap.get("settings") or {}).get("variants") or []]
        out.append({"version": version, "checksum": snap["checksum"], "variant_ids": ids})
    return out


async def queue_tests(db: Any, pb: Mapping[str, Any], *, now: Optional[datetime] = None,
                      only_missing: bool = False) -> List[str]:
    """Queue the full test-call suite with this playbook for every target version. `only_missing`:
    skip the versions it already has a test (any state) for."""
    from core.outbound import simulate

    now = now or utcnow()
    queued: List[str] = []
    known = {(t["checksum"], tuple(t["variant_ids"])) for t in pb.get("targets") or []} if only_missing else set()
    new_targets = list(pb.get("targets") or []) if only_missing else []
    for t in await targets(db, pb["offer_id"]):
        if (t["checksum"], tuple(t["variant_ids"])) in known:
            continue
        snap = await version_doc(db, pb["offer_id"], t["version"])
        if not snap:
            continue
        sim = await simulate.queue(db, offer_id=pb["offer_id"], settings_doc=snap["settings"], variant_ids=t["variant_ids"],
                                   version=t["version"], by="system:learning", now=now, playbook=pb, source="learning")
        queued.append(sim["_id"])
        new_targets.append({**t, "simulation_id": sim["_id"], "result": None})
    await db[PLAYBOOKS].update_one({"_id": pb["_id"]}, {"$set": {"targets": new_targets}})
    return queued


async def tests_status(db: Any, pb: Mapping[str, Any]) -> Tuple[str, List[str]]:
    """("pending" | "passed" | "failed" | "retry", reasons). "retry": a test couldn't run today for a
    reason that will pass (the budget), not because the playbook failed."""
    from core.outbound import simulate

    tgs = pb.get("targets") or []
    if not tgs:
        return "failed", ["There's no published offer version to test it with."]
    reasons: List[str] = []
    state = "passed"
    for t in tgs:
        sim = await db[simulate.SIMULATIONS].find_one({"_id": t.get("simulation_id")}, {"settings": 0})
        if sim is None:
            return "failed", ["Its test calls are missing."]
        if sim["status"] in ("queued", "running"):
            state = "pending"
            continue
        if sim["status"] == "failed":
            if "budget" in str(sim.get("error") or "").lower():
                return "retry", [str(sim.get("error"))]
            return "failed", [str(sim.get("error") or "The test calls couldn't run.")]
        if sim.get("passed") != sim.get("total") or not sim.get("total"):
            failed = [f"v{t['version']} {r.get('variant_id')}: {r.get('label')} — {r.get('reason')}"
                      for r in sim.get("results") or [] if not r.get("passed")]
            return "failed", failed[:6] or ["Some test calls failed."]
    return state, reasons


async def eligible(db: Any, playbook_id: Optional[str], *, version_checksum: str, variant_id: str,
                   cache: Optional[Dict[Any, Any]] = None) -> bool:
    """May calls on this exact offer version and script use this playbook? Only after it passed the
    test calls with that same text."""
    from core.outbound import simulate

    if not playbook_id:
        return False
    key = ("eligible", playbook_id, version_checksum, variant_id)
    if cache is not None and key in cache:
        return bool(cache[key])
    pb = await get(db, playbook_id)
    ok = pb is not None and await simulate.latest_pass(db, version_checksum, [variant_id],
                                                       playbook_checksum=pb["checksum"]) is not None
    if cache is not None:
        cache[key] = ok
    return ok


async def make_live(db: Any, offer_id: str, playbook_id: Optional[str], *, now: Optional[datetime] = None,
                    reason: str = "", retire_as: str = "retired") -> bool:
    """Make this playbook the one calls use (None = back to the reviewed baseline). The one it
    replaces is retired (or rolled back). False when nothing changed."""
    now = now or utcnow()
    doc = await learning_doc(db, offer_id)
    old = doc.get("live_playbook_id")
    if old == playbook_id:
        return False
    await db[LEARNING].update_one({"_id": offer_id}, {"$set": {"live_playbook_id": playbook_id, "updated_at": now}})
    if old:
        await db[PLAYBOOKS].update_one({"_id": old, "status": "live"},
                                       {"$set": {"status": retire_as, "retired_at": now, "retired_reason": reason[:300]}})
    if playbook_id:
        await db[PLAYBOOKS].update_one({"_id": playbook_id}, {"$set": {"status": "live", "live_since": now,
                                                                        "decision": {"action": "live", "reason": reason[:300],
                                                                                     "at": now}}})
    return True


async def for_call(db: Any, attempt: Mapping[str, Any], offer: Mapping[str, Any]) -> Tuple[Optional[Dict[str, Any]], bool]:
    """The playbook an attempt was assigned, ready for the prompt, and whether it was applied as
    assigned. Its entries are checked against this exact offer version once more; anything that no
    longer fits is left out rather than said."""
    pid = attempt.get("playbook_id")
    if not pid:
        return None, True                        # the baseline was assigned, and that's what runs
    try:
        doc = await get(db, pid)
        if not doc:
            return None, False
        kept, _ = validate(entries_of(doc), offer)
        return {"id": pid, "checksum": doc.get("checksum"), "lines": render(kept),
                "entry_ids": [e.id for e in kept]}, True
    except Exception:
        logger.warning("Playbook %s not loaded for attempt %s", pid, attempt.get("_id"), exc_info=True)
        return None, False


async def advance(db: Any, *, now: Optional[datetime] = None) -> Dict[str, int]:
    """Move playbooks whose test calls finished on: passed → live (a person asked for it) or ready to be
    tested on live calls; failed → failed_checks. A test that couldn't run for the budget is queued again
    the next day, three times at most. Live and testing playbooks get tests for offer versions that
    campaigns have moved to."""
    from core.outbound import simulate

    now = now or utcnow()
    stats = {"passed": 0, "failed": 0, "requeued": 0}
    for pb in await db[PLAYBOOKS].find({"status": "awaiting_tests"}).to_list(50):
        state, reasons = await tests_status(db, pb)
        if state == "pending":
            continue
        if state == "retry":
            if int(pb.get("test_retries") or 0) >= 3:
                state, reasons = "failed", reasons + ["The test calls couldn't run three days running."]
            else:
                due = pb.get("retry_after")
                if due is None:                                # wait for tomorrow's budget
                    await db[PLAYBOOKS].update_one({"_id": pb["_id"]}, {"$set": {"retry_after": now + timedelta(days=1)}})
                    continue
                if (due if due.tzinfo else due.replace(tzinfo=timezone.utc)) > now:
                    continue
                await db[PLAYBOOKS].update_one({"_id": pb["_id"]}, {"$set": {"retry_after": None},
                                                                    "$inc": {"test_retries": 1}})
                await queue_tests(db, {**pb, "targets": []}, now=now)
                stats["requeued"] += 1
                continue
        if state == "failed":
            await db[PLAYBOOKS].update_one({"_id": pb["_id"]}, {"$set": {
                "status": "failed_checks", "decision": {"action": "failed_checks", "reason": "; ".join(reasons)[:600], "at": now}}})
            stats["failed"] += 1
            continue
        stats["passed"] += 1
        if pb.get("go_live"):
            await make_live(db, pb["offer_id"], pb["_id"], now=now, reason="Passed its test calls; made live by a person")
        else:
            await db[PLAYBOOKS].update_one({"_id": pb["_id"]}, {"$set": {"status": "ready", "tests_passed_at": now}})
    # New offer versions in use: the playbooks calls use (or test) need tests for them too.
    for pb in await db[PLAYBOOKS].find({"status": {"$in": ["live", "testing"]}}).to_list(50):
        if await db[simulate.SIMULATIONS].count_documents({"playbook_id": pb["_id"], "status": {"$in": ["queued", "running"]}}):
            continue
        current = {(t["checksum"], tuple(t["variant_ids"])) for t in await targets(db, pb["offer_id"])}
        tested = {(t["checksum"], tuple(t["variant_ids"])) for t in pb.get("targets") or []}
        if current - tested:
            await queue_tests(db, pb, now=now, only_missing=True)
            stats["requeued"] += 1
    return stats
