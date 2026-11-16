"""Shapes and vocabulary of the outbound dialer (Roadmap Task 4.2)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Collections
LEADS = "sales_leads"
CAMPAIGNS = "campaigns"
ATTEMPTS = "call_attempts"
DNC = "dnc_list"
CONSENT = "consent_records"
REGISTRY = "dnc_registry"              # national / state do-not-call lists, one row per number per list
REGISTRY_META = "dnc_registry_meta"    # when each list was last loaded, and for which area codes

OWNER_PLATFORM = "platform"            # later: a tenant id, when customers get the dialer (Task 4.7)

CampaignMode = Literal["ai_cold_b2b_us", "ai_consented", "human_assisted"]
MODES: tuple[str, ...] = ("ai_cold_b2b_us", "ai_consented", "human_assisted")
CampaignStatus = Literal["draft", "running", "paused", "completed"]

LEAD_STATUSES: tuple[str, ...] = (
    "new",             # imported, not in a campaign's queue yet
    "queued",          # waiting to be dialled
    "in_progress",     # claimed by the dialer / on a call
    "callback",        # asked to be called back at next_eligible_at
    "interested", "demo_booked", "not_interested",
    "dnc",             # on the internal do-not-call list — never dialled again
    "bad_number",
    "exhausted",       # max attempts reached
    "blocked",         # the compliance gate will never pass this lead (mobile, no consent, listed…)
)
# A lead in one of these is finished: the dialer never claims it.
FINAL_LEAD_STATUSES = frozenset({"interested", "demo_booked", "not_interested", "dnc", "bad_number",
                                 "exhausted", "blocked"})
DIALLABLE_LEAD_STATUSES: tuple[str, ...] = ("callback", "queued")

LINE_TYPES: tuple[str, ...] = ("landline", "mobile", "fixed_voip", "non_fixed_voip", "toll_free", "personal",
                               "pager", "voicemail", "premium", "unknown")

AMD_CATEGORIES: tuple[str, ...] = ("human", "machine-vm", "machine-ivr", "machine-unavailable", "uncertain")

OUTCOMES: tuple[str, ...] = (
    "no_answer", "busy", "failed", "voicemail", "machine", "uncertain", "no_conversation", "gatekeeper",
    "not_interested", "callback", "interested", "demo_booked", "dnc", "wrong_number",
    "blocked_by_gate", "operator_absent", "dispatch_failed",
)
# Outcomes after which the lead is tried again (until max_attempts).
RETRY_OUTCOMES = frozenset({"no_answer", "busy", "failed", "voicemail", "machine", "uncertain",
                            "no_conversation", "gatekeeper"})
# The dial never reached a decision about the lead: it does not use up one of their attempts.
UNCOUNTED_OUTCOMES = frozenset({"blocked_by_gate", "operator_absent", "dispatch_failed"})
# A person picked up and talked.
HUMAN_OUTCOMES = frozenset({"no_conversation", "gatekeeper", "not_interested", "callback", "interested",
                            "demo_booked", "dnc", "wrong_number"})
LEAD_STATUS_FOR_OUTCOME = {"demo_booked": "demo_booked", "interested": "interested",
                           "not_interested": "not_interested", "dnc": "dnc", "wrong_number": "bad_number",
                           "callback": "callback"}

ACTIVE_ATTEMPT_STATUSES: tuple[str, ...] = ("dispatched", "dialing", "in_progress")

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")


class CallingWindow(BaseModel):
    """When a campaign may ring, in the LEAD's local time. Days: 1 = Monday … 7 = Sunday."""

    model_config = ConfigDict(extra="ignore")
    start: str = "09:30"
    end: str = "17:00"
    days: List[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5])

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not _HHMM.match(v or ""):
            raise ValueError("time must be HH:MM (24-hour)")
        return v

    @field_validator("days")
    @classmethod
    def _days(cls, v: List[int]) -> List[int]:
        days = sorted({int(d) for d in v})
        if not days or any(d < 1 or d > 7 for d in days):
            raise ValueError("days are 1 (Monday) to 7 (Sunday); at least one")
        return days

    @model_validator(mode="after")
    def _order(self) -> "CallingWindow":
        if self.start >= self.end:
            raise ValueError("the calling window must end after it starts")
        return self


class CampaignSettings(BaseModel):
    """The editable part of a campaign document."""

    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=2, max_length=120)
    mode: CampaignMode = "ai_cold_b2b_us"
    persona_ids: List[str] = Field(default_factory=lambda: ["sales_v1"], min_length=1, max_length=2)
    from_numbers: List[str] = Field(default_factory=list, max_length=20)
    calling_window: CallingWindow = Field(default_factory=CallingWindow)
    max_attempts: int = Field(default=3, ge=1, le=6)
    retry_after_hours: List[int] = Field(default_factory=lambda: [48, 96], max_length=6)
    max_concurrent: int = Field(default=2, ge=1, le=10)
    daily_cap: int = Field(default=150, ge=1, le=5000)
    voicemail_policy: Literal["hang_up", "leave_message"] = "hang_up"
    vertical: str = Field(default="", max_length=40)       # the kind of business being called, for the pitch
    notes: str = Field(default="", max_length=1000)

    @field_validator("from_numbers")
    @classmethod
    def _numbers(cls, v: List[str]) -> List[str]:
        out = list(dict.fromkeys(n.strip() for n in v if n and n.strip()))
        for n in out:
            if not _E164.match(n):
                raise ValueError(f"{n} is not an E.164 number (e.g. +15125550100)")
        return out

    @field_validator("retry_after_hours")
    @classmethod
    def _retries(cls, v: List[int]) -> List[int]:
        if any(h < 1 or h > 24 * 60 for h in v):
            raise ValueError("retry delays are in hours, 1 or more")
        return list(v) or [48]

    @field_validator("persona_ids")
    @classmethod
    def _personas(cls, v: List[str]) -> List[str]:
        from core.outbound.persona import PERSONAS

        unknown = [p for p in v if p not in PERSONAS]
        if unknown:
            raise ValueError(f"unknown persona {unknown}; use {', '.join(PERSONAS)}")
        return list(dict.fromkeys(v))

    @model_validator(mode="after")
    def _voicemail(self) -> "CampaignSettings":
        # ⚖ An AI-voice voicemail is an artificial-voice message: only where there is consent.
        if self.voicemail_policy == "leave_message" and self.mode != "ai_consented":
            raise ValueError("voicemail messages are only allowed in ai_consented campaigns")
        return self


def lead_public(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(doc)
    out["id"] = out.pop("_id")
    out.pop("gate_log", None)
    return out


def with_id(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if doc is None:
        return None
    out = dict(doc)
    out["id"] = out.pop("_id")
    return out
