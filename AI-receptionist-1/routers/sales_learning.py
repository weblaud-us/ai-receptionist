"""
The agent that learns from its calls (SALES_LEARNING_PLAN.md) — prefix /platform/sales, the sales team only.

GET    /platform/sales/offers/{id}/learning                 the offer's playbook, its history and the running test
PATCH  /platform/sales/offers/{id}/learning                 mode: autopilot | paused | off
POST   /platform/sales/offers/{id}/learning/baseline        calls go back to the reviewed baseline (no playbook)
POST   /platform/sales/offers/{id}/learning/run             the AI writes the next playbook to test, now
POST   /platform/sales/offers/{id}/experiment/stop          stop the live test (the current playbook stays)
POST   /platform/sales/offers/{id}/playbooks                a person's own playbook version (tested before use)
GET    /platform/sales/playbooks/{id}                       one playbook version
GET    /platform/sales/timing?country=AU                    when businesses pick up (the smart-retry table)
GET    /platform/sales/campaigns/{id}/scripts                how calls are split between the campaign's scripts
POST   /platform/sales/playbooks/{id}/make-live             use this version (roll forward or back), once it passed its tests
PATCH  /platform/sales/playbooks/{id}/entries/{entry_id}    pin or unpin an entry (the AI never changes a pinned one)
POST   /platform/sales/campaigns/{id}/learning-consent      allow or stop learning on a signed-off campaign

Nothing is deleted. Every change is audited.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from config import settings
from core.auth import Principal
from core.outbound import models as m
from core.outbound import offer_ai
from core.outbound import offers as of
from core.outbound import playbook as pb
from database.mongodb import get_db
from routers.sales import _audit, _campaign, _offer, _out, learning_consent, manager, utcnow, viewer
from utils.serialize import to_jsonable

sales_learning_router = APIRouter(prefix="/platform/sales", tags=["Sales learning"])


def _summary(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = _out(doc)
    out["lines"] = pb.render(pb.entries_of(doc))
    return out


async def _campaigns(offer_id: str) -> List[Dict[str, Any]]:
    from core.outbound import learning

    rows = await get_db()[m.CAMPAIGNS].find({"offer_id": offer_id, "status": {"$in": ["running", "paused", "draft"]}},
                                            {"name": 1, "status": 1, "mode": 1, "offer_version": 1, "legal_review": 1}
                                            ).to_list(100)
    return [{"id": c["_id"], "name": c.get("name"), "status": c.get("status"), "mode": c.get("mode"),
             "offer_version": c.get("offer_version"), "signed_off": m.legal_review_valid(c),
             "learning_allowed": learning.consent_ok(c),
             "learning_possible": c.get("mode") != "human_assisted"} for c in rows]


@sales_learning_router.get("/offers/{offer_id}/learning")
async def get_learning(offer_id: str, admin: Principal = Depends(viewer)):
    db = get_db()
    await _offer(offer_id)
    state = await pb.learning_doc(db, offer_id)
    live = await pb.get(db, state.get("live_playbook_id"))
    history = await db[pb.PLAYBOOKS].find({"offer_id": offer_id}).sort("n", -1).limit(20).to_list(20)
    from core.outbound import experiments as ex
    from core.outbound import learning

    experiment = await db[learning.EXPERIMENTS].find_one({"_id": state.get("experiment_id")}) if state.get("experiment_id") else None
    if experiment:
        experiment = {**experiment, "next_look": ex.next_look(experiment.get("looks_done") or []), "looks": list(ex.LOOKS),
                      "thresholds": list(ex.THRESHOLDS)}
    tests = await db[learning.EXPERIMENTS].find({"offer_id": offer_id}).sort("started_at", -1).limit(10).to_list(10)
    from datetime import timedelta

    from core.outbound import learn

    found = await learn.gather(db, offer_id, since=utcnow() - timedelta(days=30), now=utcnow())
    return to_jsonable({
        "curve": await learning.learning_curve(db, offer_id, now=utcnow()),
        "unanswered": found["unanswered"],
        "tests": [_out(t) for t in tests],
        "learning": {k: state.get(k) for k in ("mode", "live_playbook_id", "experiment_id", "share", "last_learn_at",
                                                 "last_rollback_at")},
        "live": _summary(live) if live else None,
        "playbooks": [_summary(d) for d in history],
        "experiment": _out(experiment) if experiment else None,
        "campaigns": await _campaigns(offer_id),
        "status": {**await offer_ai.learning_status(db, utcnow()), "playbooks_enabled": settings.SALES_PLAYBOOKS_ENABLED,
                   "autopilot": settings.SALES_LEARNING_AUTOPILOT, "coach": settings.SALES_COACH_ENABLED,
                   "policy": pb.LEARNING_POLICY_VERSION},
        "kinds": [{"value": k, "label": pb.KIND_LABEL[k]} for k in pb.KINDS],
        "limits": {"entries": pb.MAX_ENTRIES, "chars": pb.MAX_RENDERED, "say_words": pb.MAX_SAY_WORDS},
    })


@sales_learning_router.get("/timing")
async def pickup_times(country: str = "AU", admin: Principal = Depends(viewer)):
    """When businesses in a country pick up (first calls, last 90 days), for the heatmap and smart retries."""
    from core.outbound import timing

    db = get_db()
    table = await db[timing.STATS].find_one({"_id": f"timing:{country.upper()[:3]}"})
    countries = sorted(d["_id"].split(":", 1)[1] for d in await db[timing.STATS].find(
        {"_id": {"$regex": "^timing:"}}, {"_id": 1}).to_list(50))
    return to_jsonable({"country": country.upper(), "countries": countries, "updated_at": (table or {}).get("updated_at"),
                        "dials": (table or {}).get("dials", 0), "reached": (table or {}).get("reached", 0),
                        "rate": round(timing.base_rate(table), 4) if table else None, "cells": timing.heatmap(table),
                        "days": timing.WINDOW_DAYS})


@sales_learning_router.get("/campaigns/{campaign_id}/scripts")
async def campaign_scripts(campaign_id: str, admin: Principal = Depends(viewer)):
    """How the campaign splits calls between its scripts, and why (SALES_LEARNING_PLAN.md Phase 7)."""
    from core.outbound import bandit

    campaign = await _campaign(campaign_id)
    doc = await bandit.current(get_db(), campaign, now=utcnow()) if len(campaign.get("variant_ids") or []) >= 2 else None
    return to_jsonable({"split": campaign.get("script_split") or "even", "scripts": doc,
                        "warmup": bandit.WARMUP, "floor": bandit.FLOOR})


class ModeBody(BaseModel):
    mode: Literal["autopilot", "paused", "off"]


@sales_learning_router.patch("/offers/{offer_id}/learning")
async def set_mode(offer_id: str, body: ModeBody, request: Request, admin: Principal = Depends(manager)):
    """autopilot: the AI may write and test the next playbook. paused: the live playbook stays, nothing
    new is tried. off: calls use the reviewed baseline, no playbook."""
    db = get_db()
    await _offer(offer_id)
    before = await pb.learning_doc(db, offer_id)
    await db[pb.LEARNING].update_one({"_id": offer_id}, {"$set": {"mode": body.mode, "updated_at": utcnow(),
                                                                  "updated_by": admin.user_id}})
    await _audit(admin, request, "sales.learning.mode", "offer", offer_id, {"from": before.get("mode"), "to": body.mode})
    return await get_learning(offer_id, admin)


@sales_learning_router.post("/offers/{offer_id}/learning/baseline")
async def use_baseline(offer_id: str, request: Request, admin: Principal = Depends(manager)):
    """Calls stop using a playbook at once and go back to what the legal review covered."""
    db = get_db()
    await _offer(offer_id)
    state = await pb.learning_doc(db, offer_id)
    if state.get("experiment_id"):
        from core.outbound import learning

        await learning.stop_experiment(db, offer_id, reason="Stopped when calls went back to the baseline", by=admin.user_id)
    changed = await pb.make_live(db, offer_id, None, now=utcnow(), reason=f"Back to the baseline (by {admin.user_id})")
    await _audit(admin, request, "sales.playbook.make_live", "offer", offer_id,
                 {"playbook_id": None, "from": state.get("live_playbook_id"), "changed": changed})
    return await get_learning(offer_id, admin)


@sales_learning_router.post("/offers/{offer_id}/learning/run")
async def run_learning(offer_id: str, request: Request, admin: Principal = Depends(manager)):
    """Ask the AI to write the next playbook from recent reviewed calls now (it still has to pass its
    test calls and a live test). Charged to the learning budget."""
    from core.outbound import learn

    await _offer(offer_id)
    if not settings.OPENAI_API_KEY:
        raise HTTPException(503, "OPENAI_API_KEY is not set.")
    try:
        out = await learn.learn(get_db(), offer_id, now=utcnow(), forced=True, by=f"platform:{admin.user_id}")
    except offer_ai.AiError as e:
        raise HTTPException(e.status, str(e))
    await _audit(admin, request, "sales.learning.run", "offer", offer_id,
                 {"playbook_id": (out["playbook"] or {}).get("_id"), "note": out["note"]})
    return to_jsonable({"playbook": _summary(out["playbook"]) if out["playbook"] else None, "note": out["note"],
                        "dropped": out["dropped"][:10]})


@sales_learning_router.post("/offers/{offer_id}/experiment/stop")
async def stop_test(offer_id: str, request: Request, admin: Principal = Depends(manager)):
    """Stop the live test now: the playbook being tried is set aside and the current one stays."""
    from core.outbound import learning

    await _offer(offer_id)
    state = await pb.learning_doc(get_db(), offer_id)
    if not await learning.stop_experiment(get_db(), offer_id, reason=f"Stopped by {admin.user_id}", by=admin.user_id):
        raise HTTPException(409, "No test is running for this offer.")
    await _audit(admin, request, "sales.experiment.stop", "offer", offer_id, {"experiment_id": state.get("experiment_id")})
    return await get_learning(offer_id, admin)


class EntryIn(BaseModel):
    id: Optional[str] = Field(default=None, max_length=40)
    kind: pb.Kind
    when: str = Field(min_length=3, max_length=120)
    do: str = Field(min_length=5, max_length=240)
    say: str = Field(default="", max_length=160)
    pinned: bool = False


class PlaybookBody(BaseModel):
    entries: List[EntryIn] = Field(max_length=pb.MAX_ENTRIES)
    why: str = Field(default="", max_length=300)
    go_live: bool = True          # true: live once it passes its tests; false: tried on live calls first


def _slug(text: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:30] or "lesson"
    base = base if len(base) >= 2 else f"{base}_x"
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base[:30]}_{n}", n + 1
    return candidate


async def _offer_versions(offer_id: str) -> List[Dict[str, Any]]:
    """The offer texts a playbook must fit: the versions campaigns use, else the latest published one."""
    db = get_db()
    out = []
    for t in await pb.targets(db, offer_id):
        snap = await pb.version_doc(db, offer_id, t["version"])
        if snap:
            out.append(of.runtime(snap["settings"], offer_id, t["version"]))
    return out


def _changes(parent: List[pb.Entry], entries: List[pb.Entry]) -> List[Dict[str, Any]]:
    before = {e.id: e for e in parent}
    after = {e.id: e for e in entries}
    out: List[Dict[str, Any]] = []
    for eid, e in after.items():
        old = before.get(eid)
        if old is None:
            out.append({"op": "add", "entry_id": eid, "why": "Added by a person"})
        elif (old.kind, old.when, old.do, old.say) != (e.kind, e.when, e.do, e.say):
            out.append({"op": "edit", "entry_id": eid, "why": "Edited by a person"})
    out += [{"op": "remove", "entry_id": eid, "why": "Removed by a person"} for eid in before if eid not in after]
    return out


@sales_learning_router.post("/offers/{offer_id}/playbooks")
async def create_playbook(offer_id: str, body: PlaybookBody, request: Request, admin: Principal = Depends(manager)):
    """A person's own playbook version. It runs the full test-call suite with every offer version and
    script in use; then it goes live (go_live) or is tried on live calls against the current one."""
    db = get_db()
    await _offer(offer_id)
    versions = await _offer_versions(offer_id)
    if not versions:
        raise HTTPException(409, "Publish the offer first: a playbook is tested with a published version.")
    state = await pb.learning_doc(db, offer_id)
    parent_doc = await pb.get(db, state.get("live_playbook_id"))
    parent = pb.entries_of(parent_doc)
    by_id = {e.id: e for e in parent}
    taken: set[str] = set()
    entries: List[pb.Entry] = []
    for raw in body.entries:
        eid = raw.id if raw.id and re.fullmatch(r"[a-z0-9_]{2,40}", raw.id) and raw.id not in taken else _slug(raw.when, taken)
        taken.add(eid)
        old = by_id.get(eid)
        same = old is not None and (old.kind, old.when, old.do, old.say) == (raw.kind, raw.when, raw.do, raw.say)
        entries.append(pb.Entry(id=eid, kind=raw.kind, when=raw.when.strip(), do=raw.do.strip(), say=raw.say.strip(),
                                pinned=raw.pinned, origin=old.origin if same and old else "manual",
                                evidence=old.evidence if same and old else pb.Evidence(), since=old.since if same and old else 1))
    problems: Dict[str, List[str]] = {}
    for offer in versions:
        for e in entries:
            issues = pb.problems(e, offer)
            if issues:
                problems.setdefault(e.id, [])
                problems[e.id] += [f"v{offer['version']}: {i}" if len(versions) > 1 else i for i in issues
                                   if (f"v{offer['version']}: {i}" if len(versions) > 1 else i) not in problems[e.id]]
    kept, dropped = pb.validate(entries, versions[0])
    if problems or dropped:
        for d in dropped:
            problems.setdefault(d["entry"]["id"], [])
            problems[d["entry"]["id"]] += [p for p in d["problems"] if p not in problems[d["entry"]["id"]]]
        raise HTTPException(422, {"message": "Some lessons can't be used as written", "problems": problems})
    changes = _changes(parent, kept)
    if not changes:
        raise HTTPException(409, "That's the same as the live playbook.")
    doc = await pb.create(db, offer_id, kept, parent=state.get("live_playbook_id"), changes=changes, origin="manual",
                          by=admin.user_id, now=utcnow(), go_live=body.go_live, why=body.why)
    await pb.queue_tests(db, doc, now=utcnow())
    await _audit(admin, request, "sales.playbook.create", "playbook", doc["_id"],
                 {"changes": changes, "go_live": body.go_live, "why": body.why})
    return {"playbook": _summary(await pb.get(db, doc["_id"]) or doc)}


@sales_learning_router.get("/playbooks/{playbook_id}")
async def get_playbook(playbook_id: str, admin: Principal = Depends(viewer)):
    doc = await pb.get(get_db(), playbook_id)
    if not doc:
        raise HTTPException(404, "Playbook not found")
    return {"playbook": _summary(doc)}


@sales_learning_router.post("/playbooks/{playbook_id}/make-live")
async def make_live(playbook_id: str, request: Request, admin: Principal = Depends(manager)):
    """Use this version on calls (to roll forward, or back to an earlier one). If it hasn't passed its
    test calls with every offer version in use, they run first and it goes live when they pass."""
    db = get_db()
    doc = await pb.get(db, playbook_id)
    if not doc:
        raise HTTPException(404, "Playbook not found")
    if doc["status"] in ("testing",):
        raise HTTPException(409, "This playbook is being tried on live calls: stop the test first.")
    offer_id = doc["offer_id"]
    tested = True
    for t in await pb.targets(db, offer_id):
        for vid in t["variant_ids"]:
            if not await pb.eligible(db, playbook_id, version_checksum=t["checksum"], variant_id=vid):
                tested = False
    state = await pb.learning_doc(db, offer_id)
    if state.get("experiment_id"):
        from core.outbound import learning

        await learning.stop_experiment(db, offer_id, reason="Stopped when a person chose a playbook", by=admin.user_id)
    if tested:
        await pb.make_live(db, offer_id, playbook_id, now=utcnow(), reason=f"Made live by {admin.user_id}")
        result = "live"
    else:
        await db[pb.PLAYBOOKS].update_one({"_id": playbook_id}, {"$set": {"status": "awaiting_tests", "go_live": True,
                                                                          "targets": [], "decision": None}})
        await pb.queue_tests(db, {**doc, "targets": []}, now=utcnow())
        result = "awaiting_tests"
    await _audit(admin, request, "sales.playbook.make_live", "playbook", playbook_id,
                 {"from": state.get("live_playbook_id"), "result": result})
    return {"result": result, "playbook": _summary(await pb.get(db, playbook_id) or doc)}


class PinBody(BaseModel):
    pinned: bool


@sales_learning_router.patch("/playbooks/{playbook_id}/entries/{entry_id}")
async def pin_entry(playbook_id: str, entry_id: str, body: PinBody, request: Request, admin: Principal = Depends(manager)):
    """A pinned entry is never changed or removed by the AI. Not part of what the agent is told, so
    the playbook's tests still count."""
    db = get_db()
    doc = await pb.get(db, playbook_id)
    if not doc or not any(e.get("id") == entry_id for e in doc.get("entries") or []):
        raise HTTPException(404, "Entry not found")
    await db[pb.PLAYBOOKS].update_one({"_id": playbook_id, "entries.id": entry_id},
                                      {"$set": {"entries.$.pinned": body.pinned}})
    await _audit(admin, request, "sales.playbook.pin", "playbook", playbook_id, {"entry_id": entry_id, "pinned": body.pinned})
    return {"playbook": _summary(await pb.get(db, playbook_id) or doc)}


class ConsentBody(BaseModel):
    allowed: bool
    note: str = Field(default="", max_length=300)


@sales_learning_router.post("/campaigns/{campaign_id}/learning-consent")
async def learning_consent_route(campaign_id: str, body: ConsentBody, request: Request, admin: Principal = Depends(manager)):
    """Record, next to the campaign's legal sign-off, whether its calls may use playbooks. The
    playbook never changes the opener, prices, claims or consent; the reviewed baseline runs without it."""
    campaign = await _campaign(campaign_id)
    if not m.legal_review_valid(campaign):
        raise HTTPException(409, "This campaign has no current legal sign-off: it's recorded when the campaign starts.")
    if body.allowed and campaign.get("mode") == "human_assisted":
        raise HTTPException(409, "A person does the talking on human-assisted calls, so there's no playbook to use.")
    await get_db()[m.CAMPAIGNS].update_one({"_id": campaign_id}, {"$set": {
        "legal_review.learning": learning_consent(admin, body.allowed, body.note), "updated_at": utcnow()}})
    await _audit(admin, request, "sales.campaign.learning_consent", "campaign", campaign_id,
                 {"allowed": body.allowed, "note": body.note})
    return {"campaign": _out(await _campaign(campaign_id))}
