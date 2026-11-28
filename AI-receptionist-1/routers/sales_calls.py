"""
Single calls API (SALES_SINGLE_CALL_PLAN.md) — prefix /platform/sales, the sales team (caller and up).

POST /platform/sales/calls/prepare   a lead, or a business typed in: read its website, suggest the offer and
                                     script, fill empty details, and return the first review (10–40 s)
POST /platform/sales/calls/review    work the call out again after a change (offer, script, caller ID, details)
POST /platform/sales/calls           "Approve & call": the person approved exactly this review, so the AI calls

A sales manager may override a failed check that is one of our own rules (single_call.OVERRIDES): the
review is worked out again with `overrides`, and placing the call needs them again with a reason. Both
are kept on the attempt and in the audit log. Callers see what a manager could override.

Nothing here skips the compliance gate: the review runs it, placing the call runs it again and so does the
agent, seconds before the phone rings. The call type comes from the rules; the offer must be published, the
script one of its scripts and the caller ID one of ours, whatever the browser sends.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

import phonenumbers
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator

import routers.sales as sales_api
from core import ratelimit
from core.auth import Principal
from core.outbound import single_call
from database.mongodb import get_db
from routers.sales import _audit, _out, _room_token, caller
from utils.serialize import to_jsonable

sales_calls_router = APIRouter(prefix="/platform/sales", tags=["Sales single calls"])


class BusinessBody(BaseModel):
    website: str = Field(default="", max_length=300)
    no_website: bool = False               # they have no website of their own: don't look for one
    business_name: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=32)
    country: str = Field(default="AU", min_length=2, max_length=2)   # any country; a +number says its own
    contact_name: str = Field(default="", max_length=120)

    @field_validator("country")
    @classmethod
    def _country(cls, v: str) -> str:
        code = v.strip().upper()
        if code not in phonenumbers.SUPPORTED_REGIONS:
            raise ValueError("pick a country")
        return code


class PrepareBody(BaseModel):
    lead_id: Optional[str] = Field(default=None, max_length=60)
    business: Optional[BusinessBody] = None

    @model_validator(mode="after")
    def _one(self) -> "PrepareBody":
        if bool(self.lead_id) == bool(self.business):
            raise ValueError("pick a lead, or enter a business")
        if self.business and not (self.business.website.strip() or self.business.phone.strip()):
            raise ValueError("enter the business's website or phone number")
        if self.business and self.business.no_website and not self.business.phone.strip():
            raise ValueError("enter the phone number of a business with no website")
        return self


Override = Literal["simulation", "business_hours", "single_gap", "recently_called"]   # single_call.OVERRIDES


class ReviewBody(BaseModel):
    lead_id: str = Field(min_length=1, max_length=60)
    offer_id: str = Field(min_length=1, max_length=40)
    variant_id: str = Field(min_length=1, max_length=32)
    from_number: Optional[str] = Field(default=None, max_length=20)
    overrides: List[Override] = Field(default_factory=list, max_length=4)   # a sales manager's only


class PlaceBody(BaseModel):
    lead_id: str = Field(min_length=1, max_length=60)
    offer_id: str = Field(min_length=1, max_length=40)
    offer_version: int = Field(ge=1)
    variant_id: str = Field(min_length=1, max_length=32)
    from_number: str = Field(min_length=8, max_length=20)
    review_hash: str = Field(min_length=8, max_length=64)
    approved: bool = False                 # the person pressed "Approve & call"
    overrides: List[Override] = Field(default_factory=list, max_length=4)   # as in the review they approved
    override_reason: str = Field(default="", max_length=300)


def _refused(e: single_call.SingleCallError) -> HTTPException:
    return HTTPException(e.status, to_jsonable(e.detail))


def _lead_out(lead: Optional[Dict[str, Any]]) -> Any:
    return _out({k: v for k, v in lead.items() if k != "gate_log"}) if lead else None


@sales_calls_router.post("/calls/prepare")
async def prepare_call(body: PrepareBody, request: Request, admin: Principal = Depends(caller)):
    """Get one business ready for a single call. Reads its website and asks the AI, so it is limited per person."""
    if not await ratelimit.allow_fixed(f"single-prep:{admin.user_id}", single_call.PREPARES_PER_USER_HOUR, 3600):
        raise HTTPException(429, "Too many calls prepared this hour. Try again later.")
    try:
        result = await single_call.prepare(get_db(), lead_id=body.lead_id,
                                           business=body.business.model_dump() if body.business else None,
                                           by=admin.user_id, now=sales_api.utcnow(),
                                           may_override=admin.can_sell("manager"))
    except single_call.SingleCallError as e:
        raise _refused(e)
    if result.get("lead"):
        await _audit(admin, request, "sales.call.prepare", "sales_lead", result["lead"]["_id"],
                     {"created": result.get("created"), "callable": result.get("callable"),
                      "suggested": (result.get("suggestion") or {}).get("offer_id")})
    return to_jsonable({**result, "lead": _lead_out(result.get("lead"))})


@sales_calls_router.post("/calls/review")
async def review_call(body: ReviewBody, admin: Principal = Depends(caller)):
    """The call worked out again: after the person changed the offer, the script, the caller ID or a detail."""
    db = get_db()
    try:
        lead = await single_call.resolve(db, body.lead_id)
        result = await single_call.review(db, lead, offer_id=body.offer_id, variant_id=body.variant_id,
                                          from_number=body.from_number, by=admin.user_id, now=sales_api.utcnow(),
                                          overrides=body.overrides, may_override=admin.can_sell("manager"))
    except single_call.SingleCallError as e:
        raise _refused(e)
    return to_jsonable(result)


@sales_calls_router.post("/calls")
async def place_call(body: PlaceBody, request: Request, admin: Principal = Depends(caller)):
    """"Approve & call". The approval (who, when, and exactly what they reviewed) is kept on the attempt."""
    if not body.approved:
        raise HTTPException(422, "Approve the call first.")
    try:
        placed = await single_call.place(
            get_db(), lead_id=body.lead_id, offer_id=body.offer_id, offer_version=body.offer_version,
            variant_id=body.variant_id, from_number=body.from_number, review_hash_seen=body.review_hash,
            by=admin.user_id, by_name=admin.name or "", now=sales_api.utcnow(),
            overrides=body.overrides, override_reason=body.override_reason, may_override=admin.can_sell("manager"))
    except single_call.SingleCallError as e:
        raise _refused(e)
    attempt = placed["attempt"]
    await _audit(admin, request, "sales.call.single", "call_attempt", placed["attempt_id"],
                 {"lead_id": body.lead_id, "offer_id": body.offer_id, "offer_version": body.offer_version,
                  "variant_id": body.variant_id, "mode": attempt.get("mode"), "hash": body.review_hash,
                  "overrides": attempt.get("overrides") or [],
                  "override_reason": (attempt.get("approval") or {}).get("override_reason") or ""})
    return to_jsonable({"attempt_id": placed["attempt_id"], "room": placed["room"],
                        "attempt": _out({k: v for k, v in attempt.items() if k != "gate"}),
                        "join": _room_token(placed["room"], takeover=False, user=admin)})
