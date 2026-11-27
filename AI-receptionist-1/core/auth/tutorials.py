"""
Tutorial progress, per user: which welcome tours and getting-started lists someone has finished or skipped.

Stored on the user document as ``tutorials.<key> = {"status": "done" | "skipped", "at": datetime}``.
Keys are namespaced so both dashboards share one map without clashing:

    tour:dashboard:owner                 the owner's welcome tour (one per role, not per business)
    tour:platform:sales                  the sales team's tour in the platform app
    checklist:dashboard:getting_started:<tenant_id>   the getting-started list on one business's overview

The key pattern never allows "." or "$", so a key is always safe as a Mongo field path.
"""

from __future__ import annotations

import re
from typing import Any, Dict

from fastapi import HTTPException

from core.auth.service import _aware, utcnow
from database.mongodb import get_db

KEY_PATTERN = r"^(tour|checklist):(dashboard|platform):[a-z_]{2,24}(:[A-Za-z0-9_-]{1,64})?$"
KEY_RE = re.compile(KEY_PATTERN)
STATUSES = ("done", "skipped")
MAX_KEYS = 50


def _view(doc: Dict[str, Any] | None) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for key, v in ((doc or {}).get("tutorials") or {}).items():
        if KEY_RE.match(key) and isinstance(v, dict) and v.get("status") in STATUSES:
            out[key] = {"status": v["status"], "at": _aware(v.get("at"))}
    return out


async def get(user_id: str) -> Dict[str, Dict[str, Any]]:
    return _view(await get_db().users.find_one({"_id": user_id}, {"tutorials": 1}))


async def mark(user_id: str, key: str, status: str) -> Dict[str, Dict[str, Any]]:
    if not KEY_RE.match(key) or status not in STATUSES:
        raise HTTPException(422, "Unknown tutorial or status")
    users = get_db().users
    # One atomic write: re-marking a saved key always works; a new key only while under the cap.
    res = await users.update_one(
        {"_id": user_id, "disabled": {"$ne": True}, "$or": [
            {f"tutorials.{key}": {"$exists": True}},
            {"$expr": {"$lt": [{"$size": {"$objectToArray": {"$ifNull": ["$tutorials", {}]}}}, MAX_KEYS]}},
        ]},
        {"$set": {f"tutorials.{key}": {"status": status, "at": utcnow()}}})
    if res.matched_count == 0:
        doc = await users.find_one({"_id": user_id}, {"disabled": 1})
        if not doc or doc.get("disabled"):
            raise HTTPException(404, "User not found")
        raise HTTPException(409, "Too many saved tutorials")
    return await get(user_id)


async def reset(user_id: str, key: str) -> Dict[str, Dict[str, Any]]:
    if not KEY_RE.match(key):
        raise HTTPException(422, "Unknown tutorial")
    await get_db().users.update_one({"_id": user_id}, {"$unset": {f"tutorials.{key}": ""}})
    return await get(user_id)
