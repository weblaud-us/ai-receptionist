"""
TenantConfig — one business, as plain validated data (Roadmap Task 2.2, `tenants` collection).

Built from a Mongo document once per call / request / message and read from memory after.
Defaults are production-safe for a business that has configured nothing yet. The hotel that
existed before Phase 2 is `legacy_hotel_tenant()`, built from the old `HOTEL_*` settings, and is
used only until `migrations/m0003_hotel_to_tenant.py` has created a real `tenants` document.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from config import settings
from core.tenancy.ids import new_id

Vertical = Literal["home_services", "salon", "restaurant", "hotel", "clinic", "professional", "generic"]
VERTICALS: tuple[str, ...] = ("home_services", "salon", "restaurant", "hotel", "clinic", "professional", "generic")
# incomplete = signed up, Checkout not finished yet (Phase 5); it answers no calls.
TenantStatus = Literal["incomplete", "trial", "active", "past_due", "suspended", "cancelled"]

_HHMM = re.compile(r"^([01]\d|2[0-4]):([0-5]\d)$")
_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")

LEGACY_TENANT_ID = "default"
MAX_CUSTOM_INSTRUCTIONS = 1500


def parse_hhmm(value: str) -> time:
    m = _HHMM.match(value or "")
    if not m:
        raise ValueError(f"time must be HH:MM, got {value!r}")
    h, mi = int(m.group(1)), int(m.group(2))
    if h == 24:
        if mi != 0:
            raise ValueError("24:xx is only valid as 24:00")
        return time(23, 59, 59)
    return time(h, mi)


class HoursEntry(BaseModel):
    """One opening interval. `dow`: 0 = Monday … 6 = Sunday (Python weekday())."""

    model_config = ConfigDict(extra="ignore")

    dow: int = Field(ge=0, le=6)
    open: str = "09:00"
    close: str = "17:00"

    @field_validator("open", "close")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        parse_hhmm(v)
        return v

    @property
    def open_time(self) -> time:
        return parse_hhmm(self.open)

    @property
    def close_time(self) -> time:
        return parse_hhmm(self.close)

    @property
    def overnight(self) -> bool:
        """e.g. 20:00 → 02:00: the interval ends on the next day."""
        return self.close_time <= self.open_time


def default_hours() -> List[HoursEntry]:
    return [HoursEntry(dow=d, open="09:00", close="17:00") for d in range(5)]


class EndpointingConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    min_delay: float = Field(default=0.3, ge=0.1, le=2.0)
    max_delay: float = Field(default=2.5, ge=0.5, le=6.0)


class RecordingConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    enabled: bool = False
    retention_days: int = Field(default=30, ge=1, le=3650)
    announce: bool = True


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = "Aria"
    voice_provider: Literal["elevenlabs", "cartesia", "openai"] = "elevenlabs"
    voice_id: str = ""                       # empty → provider default from settings
    language: str = "en-US"                  # TTS / reply language
    stt_language: str = "en-US"              # "multi" for code-switching callers
    greeting: str = ""                       # empty → rendered from business + agent name
    tone: Literal["warm", "professional", "upbeat"] = "warm"
    custom_instructions: str = ""            # owner free text; fenced and length-capped in the prompt
    llm_model: str = ""                      # empty → settings.VOICE_LLM_MODEL
    endpointing: EndpointingConfig = Field(default_factory=EndpointingConfig)
    ambience: bool = False
    recording: RecordingConfig = Field(default_factory=RecordingConfig)
    max_call_minutes: int = Field(default=15, ge=1, le=120)
    transcript_retention_days: int = Field(default=90, ge=1, le=3650)   # transcripts purged after this

    @field_validator("custom_instructions")
    @classmethod
    def _cap(cls, v: str) -> str:
        return (v or "")[:MAX_CUSTOM_INSTRUCTIONS]


class TransferConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    number: str = ""                         # E.164 staff line; empty = transfers off
    only_during_hours: bool = True           # ring staff only while the business is open
    hours: str = ""                          # explicit window "HH:MM-HH:MM" (business-local) instead
    warm: bool = False


# ⚖ Words that tell a caller they are talking to an AI. A custom greeting without one gets the
# standard disclosure sentence added (TenantConfig.greeting_text).
_DISCLOSURE_RE = re.compile(
    r"\b(virtual|digital|automated|artificial|ai|a\.i\.|bot|robot|"
    r"asistente virtual|assistant virtuel|virtuelle?r? assistent(in)?|ki|assistente virtuale|"
    r"مساعد افتراضي|ذكاء اصطناعي)\b",
    re.IGNORECASE)
_FIRST_SENTENCE_RE = re.compile(r"^(.+?[.!])(\s+|$)", re.DOTALL)


def has_ai_disclosure(text: str) -> bool:
    return bool(_DISCLOSURE_RE.search(text or ""))


def add_disclosure(greeting: str, disclosure: str) -> str:
    """Put the disclosure right after the opening sentence ("Thanks for calling X."), or first when
    the greeting opens with a question, so the caller hears it before they start talking."""
    greeting = greeting.strip()
    m = _FIRST_SENTENCE_RE.match(greeting)
    if m and "?" not in m.group(1):
        rest = greeting[m.end():].strip()
        return f"{m.group(1)} {disclosure}" + (f" {rest}" if rest else "")
    return f"{disclosure} {greeting}"


_WINDOW_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


def parse_hours(spec: str) -> Optional[tuple[time, time]]:
    """'09:00-21:00' → (09:00, 21:00). Empty or malformed → None (no restriction)."""
    m = _WINDOW_RE.match(spec or "")
    if not m:
        return None
    h1, m1, h2, m2 = (int(g) for g in m.groups())
    if h2 == 24 and m2 == 0:
        h2, m2 = 23, 59
    try:
        return time(h1, m1), time(h2, m2)
    except ValueError:
        return None


def in_window(window: tuple[time, time], t: time) -> bool:
    start, end = window
    if start <= end:
        return start <= t < end
    return t >= start or t < end                 # overnight, e.g. 20:00-06:00


class NotificationsConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    owner_phone: str = ""
    owner_email: str = ""
    owner_whatsapp: str = ""
    on_new_booking: List[str] = Field(default_factory=lambda: ["sms", "email"])
    on_message: List[str] = Field(default_factory=lambda: ["sms"])
    on_urgent: List[str] = Field(default_factory=lambda: ["sms", "call"])
    daily_digest: bool = True
    monthly_report: bool = True              # "your assistant this month" (Phase 7)


class SchedulingConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    min_notice_minutes: int = Field(default=120, ge=0, le=7 * 24 * 60)
    max_days_ahead: int = Field(default=60, ge=1, le=365)
    slot_step_minutes: int = Field(default=15, ge=5, le=60)
    default_duration_minutes: int = Field(default=30, ge=5, le=480)
    cancellation_policy: str = ""           # read to the caller on cancel
    max_party_size: int = Field(default=8, ge=1, le=200)   # restaurants


class Address(BaseModel):
    model_config = ConfigDict(extra="ignore")
    line1: str = ""
    line2: str = ""
    city: str = ""
    region: str = ""
    postal: str = ""
    country: str = ""

    def one_line(self) -> str:
        parts = [self.line1, self.line2, self.city, self.region, self.postal, self.country]
        return ", ".join(p for p in parts if p)


class WhatsAppIntegration(BaseModel):
    model_config = ConfigDict(extra="ignore")
    phone_number_id: str = ""
    waba_id: str = ""


WEBHOOK_EVENTS: tuple[str, ...] = (
    "appointment.created", "appointment.cancelled", "message.created", "lead.created", "call.completed",
)


class WebhookEndpoint(BaseModel):
    """An outgoing webhook (Task 2.8 escape hatch: Zapier/Make/your own code). Empty events = all."""

    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: new_id("whk"))
    url: str
    secret: str = ""                         # HMAC-SHA256 key; shown once at creation
    events: List[str] = Field(default_factory=list)
    active: bool = True
    created_at: Optional[datetime] = None
    last_delivery: Optional[Dict[str, Any]] = None   # {"at", "event", "ok", "status"}

    @field_validator("url")
    @classmethod
    def _https(cls, v: str) -> str:
        v = (v or "").strip()
        if not re.match(r"^https?://[^\s/]+", v):
            raise ValueError("webhook url must start with http:// or https://")
        return v[:500]

    @field_validator("events")
    @classmethod
    def _known(cls, v: List[str]) -> List[str]:
        return [e for e in dict.fromkeys(v or []) if e in WEBHOOK_EVENTS]

    def wants(self, event: str) -> bool:
        return self.active and (not self.events or event in self.events)


class Integrations(BaseModel):
    model_config = ConfigDict(extra="ignore")
    whatsapp: WhatsAppIntegration = Field(default_factory=WhatsAppIntegration)
    google_calendar: Dict[str, Any] = Field(default_factory=dict)   # Task 2.8 (connected, calendar_id, token_ref)
    webhooks: List[WebhookEndpoint] = Field(default_factory=list)

    @field_validator("webhooks", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Any:
        out = []
        for item in v or []:
            if isinstance(item, str):
                item = {"url": item}
            try:
                out.append(WebhookEndpoint.model_validate(item) if isinstance(item, dict) else item)
            except ValueError:
                continue                       # a bad row never breaks the whole tenant
        return out


class Links(BaseModel):
    """Pages the assistant may text to a caller with send_info_link (Task 2.7)."""

    model_config = ConfigDict(extra="ignore")
    booking: str = ""
    menu: str = ""
    directions: str = ""                     # empty → a maps link built from the address
    payment: str = ""

    @field_validator("booking", "menu", "directions", "payment")
    @classmethod
    def _url(cls, v: str) -> str:
        v = (v or "").strip()
        if v and not re.match(r"^https?://", v, re.I):
            v = "https://" + v
        return v[:500]


class Limits(BaseModel):
    model_config = ConfigDict(extra="ignore")
    included_minutes: int = Field(default=500, ge=0)
    # Calls at once before callers hear "all lines are busy" / go to staff (Task 3.6, needs Redis).
    max_concurrent_calls: int = Field(default=3, ge=1, le=500)
    max_users: int = Field(default=0, ge=0)          # dashboard users incl. pending invites; 0 = unlimited
    max_numbers: int = Field(default=0, ge=0)        # self-serve phone numbers; 0 = unlimited


class BillingState(BaseModel):
    """Plan and subscription (Phase 5). Written by the Stripe webhook and the billing jobs, never by
    the owner directly (owners change plans in Stripe's Customer Portal). `plan="manual"` = a business
    you provisioned by hand and invoice outside Stripe: no trial cap, no Stripe sync."""

    model_config = ConfigDict(extra="ignore")
    plan: str = "manual"                     # starter | growth | pro | agency | manual
    interval: Literal["month", "year"] = "month"
    stripe_customer_id: str = ""
    stripe_subscription_id: str = ""
    subscription_status: str = ""            # Stripe's own word: trialing, active, past_due, canceled…
    trial_used: bool = False                 # one trial per business
    trial_ends_at: Optional[datetime] = None
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    period_seconds: float = 0                # billable seconds this period, refreshed at every call end
    hard_cap: bool = False                   # owner's choice: stop at 100 % instead of paying overage
    past_due_since: Optional[datetime] = None
    cancel_at_period_end: bool = False
    cancelled_at: Optional[datetime] = None
    delete_after: Optional[datetime] = None
    number_release_after: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    verified_phone: str = ""                 # owner's number, confirmed by SMS code (trial transfers)
    alerts_sent: Dict[str, List[int]] = Field(default_factory=dict)       # period key → [80, 100]
    overage_reported: Dict[str, int] = Field(default_factory=dict)        # period key → minutes sent to Stripe
    overage_pending: Dict[str, Any] = Field(default_factory=dict)         # a report not yet confirmed
    overage_last_report: Optional[datetime] = None

    @property
    def self_serve(self) -> bool:
        return self.plan != "manual"


class PilotConfig(BaseModel):
    """A free pilot business (Phase 7, Task 7.1). Set by platform admins only."""

    model_config = ConfigDict(extra="ignore")
    active: bool = False
    started_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    contact_name: str = ""
    feedback_day: str = ""                   # e.g. "Friday 10:00" — the weekly 15-minute call
    case_study_ok: bool = False              # they agreed to a testimonial / case study
    notes: str = ""


class LegalState(BaseModel):
    """What the business has agreed to (Phase 6). Written by the API, never by the owner directly."""

    model_config = ConfigDict(extra="ignore")
    dpa_version: str = ""
    dpa_accepted_at: Optional[datetime] = None
    dpa_accepted_by: str = ""                # user id


# How the business sends calls to the assistant. Decides which answered calls count as "would have
# been missed" in the results report (Phase 7): with overflow or after-hours forwarding every call
# the assistant answers is one the business couldn't take.
AnsweringMode = Literal["all_calls", "overflow", "after_hours"]


class HotelSettings(BaseModel):
    """Hotel vertical plug-in settings (rooms and nights live in their own collections)."""

    model_config = ConfigDict(extra="ignore")
    checkin_time: str = "14:00"
    checkout_time: str = "12:00"


class TenantConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str = Field(alias="_id")
    name: str
    slug: str
    vertical: str = "generic"
    status: TenantStatus = "trial"
    region: Literal["us", "eu", "me"] = "us"
    country: str = "US"
    timezone: str = "UTC"
    locale: str = "en-US"
    currency: str = "USD"
    phone: str = ""                          # the business's public number (read to callers)
    website: str = ""
    address: Address = Field(default_factory=Address)
    description: str = ""                    # one sentence about the business, used in the prompt
    agent: AgentConfig = Field(default_factory=AgentConfig)
    hours: List[HoursEntry] = Field(default_factory=default_hours)
    transfer: TransferConfig = Field(default_factory=TransferConfig)
    emergency_instructions: str = ""
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    scheduling: SchedulingConfig = Field(default_factory=SchedulingConfig)
    integrations: Integrations = Field(default_factory=Integrations)
    links: Links = Field(default_factory=Links)
    limits: Limits = Field(default_factory=Limits)
    billing: BillingState = Field(default_factory=BillingState)
    answering_mode: AnsweringMode = "all_calls"
    pilot: PilotConfig = Field(default_factory=PilotConfig)
    legal: LegalState = Field(default_factory=LegalState)
    hotel: HotelSettings = Field(default_factory=HotelSettings)
    policies: Dict[str, str] = Field(default_factory=dict)   # e.g. {"parking": "...", "payment": "..."}
    onboarding: Dict[str, Any] = Field(default_factory=dict)  # wizard progress: {"step": "...", "completed_at": ...}
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _SLUG.match(v or ""):
            raise ValueError("slug must be lowercase letters, digits and dashes")
        return v

    @field_validator("vertical")
    @classmethod
    def _vertical(cls, v: str) -> str:
        return v if v in VERTICALS else "generic"

    # ── Derived ─────────────────────────────────────────────────────────

    @property
    def tenant_id(self) -> str:
        return self.id

    @property
    def is_legacy(self) -> bool:
        return self.id == LEGACY_TENANT_ID

    @property
    def active(self) -> bool:
        return self.status in ("trial", "active", "past_due")

    @property
    def llm_model(self) -> str:
        return self.agent.llm_model or settings.VOICE_LLM_MODEL

    @property
    def voice_id(self) -> str:
        return self.agent.voice_id or settings.ELEVENLABS_VOICE_ID

    @property
    def address_text(self) -> str:
        return self.address.one_line()

    def disclosure_sentence(self) -> str:
        return f"This is {self.agent.name}, {self.name}'s virtual assistant."

    def greeting_text(self) -> str:
        """⚖ AI disclosure in the first sentence (EU AI Act, US state bot laws, UAE TDRA). ≤ 4 s.
        An owner's custom greeting that doesn't say it is an AI gets the disclosure added."""
        custom = self.agent.greeting.strip()
        if custom:
            text = custom if has_ai_disclosure(custom) else add_disclosure(custom, self.disclosure_sentence())
        else:
            text = (f"Thanks for calling {self.name}, this is {self.agent.name}, "
                    f"{self.name}'s virtual assistant.")
        if self.agent.recording.enabled and self.agent.recording.announce and "record" not in text.lower():
            text += " This call may be recorded."
        if "?" not in text:
            text += " How can I help?"
        return text

    def hours_for(self, dow: int) -> List[HoursEntry]:
        return [h for h in self.hours if h.dow == dow]

    def is_open(self, local_now: datetime) -> bool:
        """Open right now, in business-local time (handles overnight intervals)."""
        t = local_now.time()
        today, yesterday = local_now.weekday(), (local_now.weekday() - 1) % 7
        for h in self.hours_for(today):
            if h.overnight:
                if t >= h.open_time:
                    return True
            elif h.open_time <= t < h.close_time:
                return True
        for h in self.hours_for(yesterday):
            if h.overnight and t < h.close_time:
                return True
        return False

    def transfer_open(self, local_now: datetime) -> bool:
        if not self.transfer.number:
            return False
        window = parse_hours(self.transfer.hours)
        if window is not None:
            return in_window(window, local_now.time())
        if not self.transfer.only_during_hours:
            return True
        return self.is_open(local_now)

    def to_doc(self) -> Dict[str, Any]:
        doc = self.model_dump(by_alias=True)
        return doc

    @classmethod
    def from_doc(cls, doc: Dict[str, Any]) -> "TenantConfig":
        return cls.model_validate(doc)


def legacy_hotel_tenant() -> TenantConfig:
    """The pre-Phase-2 hotel, from `HOTEL_*` settings. Replaced by migration m0003."""
    now = datetime.now(timezone.utc)
    return TenantConfig(
        _id=LEGACY_TENANT_ID,
        name=settings.HOTEL_NAME,
        slug="default",
        vertical="hotel",
        status="active",
        timezone=settings.HOTEL_TIMEZONE,
        currency=settings.HOTEL_CURRENCY,
        phone=settings.HOTEL_PHONE,
        address=Address(line1=settings.HOTEL_ADDRESS),
        agent=AgentConfig(
            name=settings.RECEPTIONIST_NAME,
            voice_id=settings.ELEVENLABS_VOICE_ID,
            language=settings.VOICE_LANGUAGE,
            stt_language=settings.VOICE_LANGUAGE,
            llm_model=settings.VOICE_LLM_MODEL,
            recording=RecordingConfig(enabled=settings.ENABLE_CALL_RECORDING),
            endpointing=EndpointingConfig(min_delay=settings.VOICE_ENDPOINTING_MIN_DELAY,
                                          max_delay=settings.VOICE_ENDPOINTING_MAX_DELAY),
            ambience=settings.VOICE_AMBIENT_SOUND,
        ),
        hours=[HoursEntry(dow=d, open="00:00", close="24:00") for d in range(7)],   # a hotel never closes
        transfer=TransferConfig(number=settings.TRANSFER_PHONE_NUMBER.strip(), only_during_hours=False,
                                hours=settings.TRANSFER_HOURS.strip()),
        notifications=NotificationsConfig(owner_whatsapp=settings.OWNER_NOTIFY_WHATSAPP.strip()),
        integrations=Integrations(whatsapp=WhatsAppIntegration(
            phone_number_id=settings.META_WA_PHONE_NUMBER_ID, waba_id=settings.META_WABA_ID)),
        hotel=HotelSettings(checkin_time=settings.HOTEL_CHECKIN_TIME, checkout_time=settings.HOTEL_CHECKOUT_TIME),
        created_at=now, updated_at=now,
    )
