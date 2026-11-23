"""
The business a call is for, as plain data — built once at call start, read from memory after.

Phase 2: the profile wraps the tenant that owns the dialled number (`sip.trunkPhoneNumber`),
plus a snapshot of its services, staff and FAQs for the prompt. `resolve_profile()` is the
async lookup the worker uses; `load_profile()` is the synchronous legacy-hotel profile that
tests and evals build without a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from core.prompts.builder import BusinessSnapshot
from core.prompts.verticals import get_vertical
from core.repositories import Repos
from core.services.snapshot import load_snapshot
from core.tenancy import resolve_by_id, resolve_by_phone_number
from core.tenancy.models import TenantConfig, legacy_hotel_tenant, parse_hours  # noqa: F401  (re-exported)
from core.timeutils import business_now
from database.mongodb import get_db

UNASSIGNED_MESSAGE = "Sorry, this number isn't set up yet. Please try again later. Goodbye."
# Deepgram Nova-3 keyterm prompting takes a bounded list (⚠ VERIFY the current limit); names first.
MAX_KEYTERMS = 100


def _language_base(language: str) -> str:
    """'en-US' → 'en'; 'multi' → 'en' (TTS needs a concrete language)."""
    base = (language or "en").split("-")[0].lower()
    return "en" if base == "multi" else base


@dataclass(frozen=True)
class VoiceProfile:
    tenant: TenantConfig
    snapshot: BusinessSnapshot = field(default_factory=BusinessSnapshot)

    # ── Flat accessors (the agent never reaches into nested config) ─────
    @property
    def business_id(self) -> str:
        return self.tenant.id

    @property
    def business_name(self) -> str:
        return self.tenant.name

    @property
    def business_type(self) -> str:
        return self.tenant.vertical

    @property
    def agent_name(self) -> str:
        return self.tenant.agent.name

    @property
    def timezone(self) -> str:
        return self.tenant.timezone

    @property
    def stt_language(self) -> str:
        return self.tenant.agent.stt_language or "en-US"

    @property
    def tts_language(self) -> str:
        return _language_base(self.tenant.agent.language)

    @property
    def llm_model(self) -> str:
        return self.tenant.llm_model

    @property
    def voice_id(self) -> str:
        return self.tenant.voice_id

    @property
    def voice_provider(self) -> str:
        return self.tenant.agent.voice_provider

    @property
    def transfer_number(self) -> str:
        return self.tenant.transfer.number

    @property
    def large_kb(self) -> bool:
        return self.snapshot.large_kb

    @property
    def keyterms(self) -> tuple[str, ...]:
        """Words STT should expect: business and agent names, vertical vocabulary, service names."""
        spec = get_vertical(self.tenant.vertical)
        words = [self.tenant.name, self.tenant.agent.name, *spec.keyterms, *self.tenant.agent.stt_keyterms]
        words += [s["name"] for s in self.snapshot.services[:20] if s.get("name")]
        words += [r["name"] for r in self.snapshot.resources[:20] if r.get("name")]
        seen: set[str] = set()
        out: list[str] = []
        for w in words:
            if w and w.lower() not in seen:
                seen.add(w.lower())
                out.append(w)
        return tuple(out[:MAX_KEYTERMS])

    @property
    def greeting_text(self) -> str:
        return self.tenant.greeting_text()

    def greeting_for(self, now: Optional[datetime] = None) -> str:
        """The greeting for a call starting at `now` (dental: lunch break / closed / overflow)."""
        return self.tenant.greeting_text(business_now(self.tenant.timezone, now))

    def transfer_open(self, now: Optional[datetime] = None) -> bool:
        """True when transfer_to_human may ring staff right now (business-local time)."""
        return self.tenant.transfer_open(business_now(self.tenant.timezone, now))


def load_profile(dialed_number: Optional[str] = None) -> VoiceProfile:
    """The legacy .env hotel, without a database (tests, evals, the pre-migration deployment)."""
    return VoiceProfile(tenant=legacy_hotel_tenant())


def unassigned_profile() -> VoiceProfile:
    """What a caller to a number nobody owns gets: a neutral message, then goodbye."""
    t = TenantConfig(_id="unassigned", name="this number", slug="unassigned", vertical="generic")
    t.agent.greeting = UNASSIGNED_MESSAGE
    return VoiceProfile(tenant=t)


async def resolve_profile(*, dialed_number: Optional[str] = None,
                          tenant_id: Optional[str] = None) -> Optional[VoiceProfile]:
    """
    The tenant for this call, with its business snapshot. None = unknown number / unknown tenant.
    Phone calls resolve by the dialled number; browser calls carry the tenant id in the token.
    """
    tenant = await resolve_by_id(tenant_id) if tenant_id else await resolve_by_phone_number(dialed_number)
    if tenant is None or tenant.billing.deleted_at:
        return None
    if not tenant.active:
        # Suspended / cancelled (inside the grace period): the worker forwards the call or asks the caller
        # to call back (core/billing/usage.gate) — no snapshot needed. Not-yet-subscribed: unknown number.
        return VoiceProfile(tenant=tenant) if tenant.status in ("suspended", "cancelled") else None
    snapshot = await load_snapshot(Repos(get_db(), tenant.id), tenant)
    return VoiceProfile(tenant=tenant, snapshot=snapshot)
