"""
Getting started: the first things a new business does, each ticked off from real data.

`signals()` answers every item for a business (the platform's launch checklist shows them all);
`items_for()` keeps the ones a given person can act on (the practice's own overview). Labels and
links live in the dashboards, where the copy belongs. Every check is one small indexed lookup.
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.prompts.verticals import get_vertical
from core.repositories import Repos
from core.repositories.repos import PhoneNumbersStore, UsersStore
from core.services import calendar_sync
from core.services.insights import NOT_ANSWERED
from core.tenancy.models import TenantConfig

ORDER = ("setup", "test_call", "number", "first_call", "knowledge", "calendar", "team", "dpa")
OWNER_ONLY = frozenset({"team", "dpa"})          # inviting and the DPA are the owner's (tenant.py, compliance.py)
SCHEDULING_ONLY = frozenset({"calendar"})


async def signals(db: Any, tenant: TenantConfig) -> Dict[str, bool]:
    if tenant.is_legacy:
        return {k: False for k in ORDER}
    repos = Repos(db, tenant.id)
    # Browser test calls use LiveKit rooms named test_<hex> (POST /t/test-call); the room is the call_id.
    test_call = await repos.calls.find_one({"call_id": {"$regex": "^test_"}}, {"_id": 1})
    # A real answered call, counted as the Results report counts it (core/services/insights.py).
    first_call = await repos.calls.find_one({
        "direction": {"$ne": "outbound"},
        "outcome": {"$nin": list(NOT_ANSWERED)},
        "call_id": {"$not": {"$regex": "^test_"}},
        "$or": [{"channel": "phone"}, {"channel": {"$exists": False}}, {"channel": None}],
    }, {"_id": 1})
    # The wizard's examples are source="template"; only an answer the practice wrote itself counts.
    knowledge = await repos.knowledge.find_one({"source": {"$ne": "template"}}, {"_id": 1})
    numbers = await PhoneNumbersStore(db).for_tenant(tenant.id)
    members = await UsersStore(db).for_tenant(tenant.id)
    invited = await db.invites.find_one({"tenant_id": tenant.id}, {"_id": 1})
    pms = tenant.integrations.pms or {}
    return {
        "setup": bool((tenant.onboarding or {}).get("completed_at")),
        "test_call": test_call is not None,
        "number": any(n.get("status", "active") == "active" for n in numbers),
        "first_call": first_call is not None,
        "knowledge": knowledge is not None,
        "calendar": calendar_sync.connected(tenant) or bool(pms.get("connected")),
        "team": len(members) > 1 or invited is not None,
        "dpa": tenant.legal.dpa_accepted_at is not None,
    }


def items_for(sig: Dict[str, bool], *, owner: bool, scheduling: bool) -> List[Dict[str, Any]]:
    keep = [k for k in ORDER
            if (owner or k not in OWNER_ONLY) and (scheduling or k not in SCHEDULING_ONLY)]
    return [{"id": k, "done": bool(sig.get(k))} for k in keep]


def summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    done = sum(1 for i in items if i["done"])
    return {"items": items, "done": done, "total": len(items), "complete": bool(items) and done == len(items)}


async def for_member(db: Any, tenant: TenantConfig, *, owner: bool) -> Dict[str, Any]:
    if tenant.is_legacy:
        return summary([])
    items = items_for(await signals(db, tenant), owner=owner,
                      scheduling=get_vertical(tenant.vertical).uses_scheduling)
    return summary(items)
