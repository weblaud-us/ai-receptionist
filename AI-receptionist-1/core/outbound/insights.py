"""
Learn from real calls (SALES_SUITE_PLAN.md Phase 14): what prospects actually objected to, whether
the offer has an answer for it, and which script is winning.

- The numbers are worked out in code (`funnel.by_variant`). A script is only called "leading" when
  every script compared has reached at least MIN_REACHED people and the meeting rates differ by at
  least MIN_LEAD_POINTS percentage points; otherwise it says there aren't enough calls yet.
- The model sees only short text already extracted after each call — the qualification notes'
  objections and pains, the not-interested reason and the call summary — never raw transcripts.
  It groups the objections, says which ones the offer already answers, and suggests answers and
  discovery questions. Suggestions go into the editor's before/after dialog like any AI suggestion.
- Needs at least MIN_CALLS answered calls. Cached for a day per offer, window and number of calls.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional

from core.outbound import funnel
from core.outbound import models as m
from core.outbound import offer_ai

CACHE = "sales_offer_insights"
MIN_CALLS = 5
MIN_REACHED = 30
MIN_LEAD_POINTS = 5.0
CACHE_FOR = timedelta(hours=24)


def compare_scripts(rows: List[Mapping[str, Any]]) -> Dict[str, Any]:
    """Meeting rate per script, and a leader only when the sample and the gap say so."""
    variants = funnel.by_variant(rows)
    reached = [v for v in variants if v["human"] >= MIN_REACHED]
    leader = None
    note = f"Not enough calls yet to compare scripts: each needs {MIN_REACHED} people reached."
    if len(variants) >= 2 and len(reached) == len(variants):
        ranked = sorted(variants, key=lambda v: v["demo_rate"] or 0, reverse=True)
        gap = ((ranked[0]["demo_rate"] or 0) - (ranked[1]["demo_rate"] or 0)) * 100
        if gap >= MIN_LEAD_POINTS:
            leader = ranked[0]["variant_id"]
            note = f"“{leader}” is ahead by {gap:.1f} points of meetings per person reached."
        else:
            note = f"The scripts are within {MIN_LEAD_POINTS:.0f} points of each other: no clear winner yet."
    elif len(variants) < 2:
        note = "Only one script has been used. Add a second one to A/B test."
    return {"variants": variants, "leader": leader, "note": note}


def _text_items(rows: List[Mapping[str, Any]]) -> List[str]:
    out = []
    for r in rows:
        q = r.get("qualification") or {}
        bits = [*(q.get("objections") or []), *(f"pain: {p}" for p in q.get("pains") or [])]
        reason = r.get("reason") if r.get("outcome") == "not_interested" else None   # finish_attempt's field
        if reason:
            bits.append(f"not interested: {reason}")
        if r.get("summary"):
            bits.append(f"summary: {str(r['summary'])[:300]}")
        review = r.get("review") or {}                     # the AI coach's notes (SALES_LEARNING_PLAN.md)
        bits += [f"asked: {q}" for q in (review.get("unanswered") or [])[:2]]
        bits += [f"lesson: {x.get('do')}" for x in (review.get("lessons") or [])[:1] if isinstance(x, Mapping)]
        if bits:
            out.append(f"[{r.get('outcome')}] " + " | ".join(str(b)[:200] for b in bits))
    return out[:150]


async def insights(db: Any, offer: Mapping[str, Any], *, days: int = 30, now: Optional[datetime] = None,
                   llm: Optional[offer_ai.Llm] = None, use_ai: bool = True,
                   version: Optional[int] = None) -> Dict[str, Any]:
    """`version`: only calls made with that published version (default: every version of the offer)."""
    now = now or datetime.now(timezone.utc)
    offer_id = str(offer.get("id") or offer.get("_id"))
    query: Dict[str, Any] = {"created_at": {"$gte": now - timedelta(days=days)}, "status": "done"}
    query["offer_id"] = offer_id if offer_id != "off_laudline" else {"$in": [None, "off_laudline"]}
    if version:
        query["offer_version"] = int(version)
    rows = await db[m.ATTEMPTS].find(query, {"status": 1, "outcome": 1, "amd": 1, "offer_id": 1, "offer_version": 1,
                                             "variant_id": 1, "persona_id": 1, "qualification": 1, "summary": 1,
                                             "reason": 1, "review.unanswered": 1, "review.lessons": 1,
                                             **funnel.NEXT_STEP_FIELDS}).to_list(20_000)
    answered = [r for r in rows if r.get("amd") == "human" or r.get("outcome") in m.HUMAN_OUTCOMES]
    base = {"days": days, "version": version, "calls": len(rows), "answered": len(answered),
            "scripts": compare_scripts(rows),
            "outcomes": {o: sum(1 for r in rows if r.get("outcome") == o) for o in sorted({str(r.get("outcome")) for r in rows})}}
    if len(answered) < MIN_CALLS or not use_ai:
        return {**base, "objections": [], "suggestions": None,
                "note": f"Needs at least {MIN_CALLS} answered calls for the AI to read ({len(answered)} so far)."}
    key = f"{offer_id}:{offer.get('version')}:{version or 'all'}:{days}:{len(answered)}"
    cached = await db[CACHE].find_one({"_id": key})
    if cached and cached.get("at") and (cached["at"] if cached["at"].tzinfo else cached["at"].replace(tzinfo=timezone.utc)) > now - CACHE_FOR:
        return {**base, **cached["ai"], "cached": True}
    known = [o.get("objection") for o in offer.get("objections") or []]
    asked = (
        "These are notes from real sales calls for one offer (already summarised; one line per call). Group what "
        "prospects objected to or said, count how often each came up, say whether the offer's existing objection "
        "list already covers it, and suggest short spoken answers for the uncovered ones and discovery questions "
        "that would surface them earlier. Use only what the notes say.\n"
        f"Existing objections in the offer: {json.dumps(known, ensure_ascii=False)}\n"
        "<offer_draft>\n" + offer_ai._fence("\n".join(_text_items(answered))) + "\n</offer_draft>\n\n"
        'Reply with JSON {"objections": [{"text": str, "count": int, "covered": bool}] (at most 8, most common first), '
        '"suggestions": {"objections": [{"objection": str, "response": str}] (at most 4, uncovered only), '
        '"discovery_questions": [str] (at most 3), "pain_hypotheses": [str] (at most 3)}, "note": str}')
    raw, usd = await offer_ai._ask(db, "insights", [{"role": "system", "content": offer_ai.rules("AU")},
                                                    {"role": "user", "content": asked}],
                                   max_tokens=1200, temperature=0.2, llm=llm, now=now)
    objections = []
    listed = raw.get("objections")
    for o in listed if isinstance(listed, list) else []:
        if isinstance(o, Mapping) and o.get("text"):
            try:
                count = max(0, min(len(answered), int(o.get("count") or 0)))
            except (TypeError, ValueError):
                count = 0
            objections.append({"text": offer_ai.clean_text(o["text"], "objection.objection"), "count": count,
                               "covered": bool(o.get("covered"))})
    sug: Mapping[str, Any] = raw["suggestions"] if isinstance(raw.get("suggestions"), Mapping) else {}
    ai = {"objections": objections[:8],
          "suggestions": {"objections": offer_ai.clean_objections(sug.get("objections"))[:4],
                          "discovery_questions": offer_ai.clean_list(sug.get("discovery_questions"), limit=3),
                          "pain_hypotheses": offer_ai.clean_list(sug.get("pain_hypotheses"), limit=3)},
          "note": offer_ai.clean_text(raw.get("note"), "what_we_sell") if raw.get("note") else "", "cost_usd": usd}
    await db[CACHE].update_one({"_id": key}, {"$set": {"ai": ai, "at": now}}, upsert=True)
    return {**base, **ai}
