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
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from config import settings
from core.tenancy.ids import new_id

Vertical = Literal["home_services", "salon", "restaurant", "hotel", "clinic", "dental", "professional", "generic"]
VERTICALS: tuple[str, ...] = ("home_services", "salon", "restaurant", "hotel", "clinic", "dental", "professional",
                               "generic")
# incomplete = signed up, Checkout not finished yet (Phase 5); it answers no calls.
TenantStatus = Literal["incomplete", "trial", "active", "past_due", "suspended", "cancelled"]

_HHMM = re.compile(r"^([01]\d|2[0-4]):([0-5]\d)$")
_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")

LEGACY_TENANT_ID = "default"
MAX_CUSTOM_INSTRUCTIONS = 1500
MAX_ANNOUNCE_TEXT = 200
MAX_STT_KEYTERMS = 50
MAX_STT_KEYTERM_LEN = 40


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
    announce_text: str = ""                  # owner's own recording notice; empty → "This call may be recorded."

    @field_validator("announce_text")
    @classmethod
    def _cap(cls, v: str) -> str:
        return " ".join((v or "").split())[:MAX_ANNOUNCE_TEXT]


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
    stt_keyterms: List[str] = Field(default_factory=list)   # owner's extra words STT should expect

    @field_validator("custom_instructions")
    @classmethod
    def _cap(cls, v: str) -> str:
        return (v or "")[:MAX_CUSTOM_INSTRUCTIONS]

    @field_validator("stt_keyterms", mode="before")
    @classmethod
    def _keyterms(cls, v: Any) -> List[str]:
        out: List[str] = []
        seen: set = set()
        for raw in v or []:
            term = " ".join(str(raw or "").split())[:MAX_STT_KEYTERM_LEN]
            if term and term.lower() not in seen:
                seen.add(term.lower())
                out.append(term)
        return out[:MAX_STT_KEYTERMS]


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
    # Dental PMS (Cliniko / Dentally): provider, connected, api_key_enc, business_id / site_id, last_sync_at…
    # Written only by core/services/pms_sync.py; the encrypted key never leaves the server.
    pms: Dict[str, Any] = Field(default_factory=dict)
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


# Query parameters that belong to one visitor's browsing session, or to ad tracking, never to the page.
# A link copied from a website often carries them; texted to a caller it opens an expired session and
# hands every caller the practice's portal login ids.
_VOLATILE_PARAM = re.compile(
    r"(?i)^(utm_.*|fbclid|gclid|gbraid|wbraid|msclkid|mc_eid|mc_cid|_ga|_gl|sid|jsessionid|phpsessid"
    r"|.*session.*|.*random.*|userid|user_id|weblogin|sourceref|sourceid)$")
# Booking portals whose path already names the practice; their query string is session state only.
_PATH_ONLY_HOSTS = re.compile(r"(?i)(^|\.)centaurportal\.com$")


def clean_link(url: str) -> str:
    """A shareable version of a link: scheme added, session and tracking parameters removed."""
    v = (url or "").strip()
    if not v:
        return ""
    if not re.match(r"^https?://", v, re.I):
        v = "https://" + v
    try:
        parts = urlsplit(v)
    except ValueError:
        return v[:500]
    params = parse_qsl(parts.query, keep_blank_values=True)
    kept = [(k, val) for k, val in params if not _VOLATILE_PARAM.match(k)]
    if _PATH_ONLY_HOSTS.search(parts.hostname or ""):
        query = ""
    elif len(kept) == len(params):
        query = parts.query                    # nothing to drop: keep it exactly as typed
    else:
        query = urlencode(kept, safe=":/")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))[:500]


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
        return clean_link(v)


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

# Where in the business day a call lands (business-local time). A gap of up to this long between two
# of today's opening intervals is a break (lunch), not "closed for the day".
AnswerWindow = Literal["open", "lunch_break", "after_hours"]
MAX_BREAK_MINUTES = 180
DEFAULT_RECORDING_NOTICE = "This call may be recorded."
DENTAL_RECORDING_NOTICE = "Just so you know, this call may be recorded."


class HotelSettings(BaseModel):
    """Hotel vertical plug-in settings (rooms and nights live in their own collections)."""

    model_config = ConfigDict(extra="ignore")
    checkin_time: str = "14:00"
    checkout_time: str = "12:00"


class DentalSettings(BaseModel):
    """Dental vertical plug-in settings (AU private health funds, UK NHS / private mix)."""

    model_config = ConfigDict(extra="ignore")
    accepts_nhs: bool = False                   # UK: any NHS dentistry at all
    accepts_new_nhs_patients: bool = False      # UK: taking on new NHS patients (most mixed practices don't)
    accepts_private_health_funds: bool = False  # AU: HICAPS on-the-spot claiming
    finance_available: bool = False             # payment plans / treatment finance


MAX_RECOVERY_TEMPLATE = 320


class MissedCallRecovery(BaseModel):
    """One text to a caller who hung up before booking or leaving a message (core/services/recovery.py).
    `template` may use {business}, {link} and {phone}; empty = the standard wording."""

    model_config = ConfigDict(extra="ignore")
    enabled: bool = False
    template: str = ""
    # ⚠ WhatsApp free text only reaches someone who messaged the business in the last 24 h; outside it
    # Meta needs an approved template. "whatsapp" falls back to SMS when WhatsApp can't deliver.
    channel: Literal["sms", "whatsapp"] = "sms"

    @field_validator("template")
    @classmethod
    def _cap(cls, v: str) -> str:
        return " ".join((v or "").split())[:MAX_RECOVERY_TEMPLATE]


def abn_valid(value: str) -> bool:
    """Australian Business Number: 11 digits, weighted checksum (ATO algorithm)."""
    d = [int(c) for c in re.sub(r"\s", "", value or "") if c.isdigit()]
    if len(d) != 11 or len(re.sub(r"[\s\d]", "", value or "")):
        return False
    d[0] -= 1
    weights = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)
    return sum(w * x for w, x in zip(weights, d)) % 89 == 0


_COMPANY_NUMBER = re.compile(r"^(\d{8}|(SC|NI|OC|SO|NC|R0|FC)\d{6})$")


class LegalEntity(BaseModel):
    """Who the business legally is: needed for AU (+61) and UK (+44) number regulatory bundles."""

    model_config = ConfigDict(extra="ignore")
    registered_name: str = ""
    abn: str = ""                            # Australia: Australian Business Number
    company_number: str = ""                 # UK: Companies House registration number

    @field_validator("registered_name")
    @classmethod
    def _name(cls, v: str) -> str:
        return " ".join((v or "").split())[:200]

    @field_validator("abn")
    @classmethod
    def _abn(cls, v: str) -> str:
        v = re.sub(r"\s", "", v or "")
        if v and not abn_valid(v):
            raise ValueError("ABN must be a valid 11-digit Australian Business Number")
        return v

    @field_validator("company_number")
    @classmethod
    def _company(cls, v: str) -> str:
        v = re.sub(r"\s", "", v or "").upper()
        if v and not _COMPANY_NUMBER.match(v):
            raise ValueError("Companies House number must be 8 characters, e.g. 01234567 or SC123456")
        return v


# ── Patient recall (dental reactivation, SMS-first) ──────────────────────────

DEFAULT_RECALL_TEMPLATE = ("Hi {first_name}, it's been a while since your last visit to {clinic_name}. You can book "
                           "your next check-up online here: {booking_link}. Reply STOP to opt out.")
RECALL_PLACEHOLDERS = ("first_name", "clinic_name", "booking_link")
MAX_RECALL_TEMPLATE = 320
# ⚖ No health information in a text (it is read on lock screens and shared phones): no conditions,
# diagnoses or sensitive procedures. A neutral "check-up" recall is the only purpose allowed, which is
# also what keeps UK PECR soft opt-in to "similar services".
_RECALL_PHI_RE = re.compile(
    r"\b(root[\s-]?canals?|gum disease|gingivitis|periodont\w*|extractions?|extracted|abscess\w*|infections?|"
    r"decay|cavit(y|ies)|dentures?|implants?|cancer|biops(y|ies)|wisdom teeth|wisdom tooth|surgery|surgical|"
    r"sedation|anxiety|pain|bleeding|swelling|swollen|diagnos\w*|treatment plan|x-?rays?|crowns?|fillings?|"
    r"veneers?|braces|invisalign|aligners?|whitening|bonding|hygiene problems?|bad breath|halitosis|pregnan\w*|"
    r"diabet\w*|hiv|medication|antibiotics?)\b",
    re.IGNORECASE)
_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")


def recall_template_problems(template: str) -> List[str]:
    """Why a recall text template can't be used ([] = fine)."""
    problems: List[str] = []
    text = " ".join((template or "").split())
    unknown = sorted({p for p in _PLACEHOLDER_RE.findall(text) if p not in RECALL_PLACEHOLDERS})
    if unknown:
        problems.append("Unknown placeholder(s): " + ", ".join("{" + u + "}" for u in unknown)
                        + ". Use {first_name}, {clinic_name} and {booking_link}.")
    if "{clinic_name}" not in text:
        problems.append("Keep {clinic_name} in the text so patients know who it is from.")
    if "stop" not in text.lower():
        problems.append("Keep the opt-out instruction, e.g. \"Reply STOP to opt out.\"")
    phi = sorted({m.group(0).lower() for m in _RECALL_PHI_RE.finditer(text)})
    if phi:
        problems.append("Recall texts can't mention treatments or health details (" + ", ".join(phi)
                        + "). Keep it to a general check-up.")
    if len(text) > MAX_RECALL_TEMPLATE:
        problems.append(f"Keep it under {MAX_RECALL_TEMPLATE} characters.")
    return problems


def _clamp_hhmm(v: str, lo: str = "08:00", hi: str = "20:00") -> str:
    parse_hhmm(v)
    return min(max(v, lo), hi)


class RecallSendWindow(BaseModel):
    """When recall texts may go out, practice-local. Never Sundays; never outside 08:00–20:00."""

    model_config = ConfigDict(extra="ignore")
    weekday: List[str] = Field(default_factory=lambda: ["09:00", "18:00"])
    saturday: Optional[List[str]] = Field(default_factory=lambda: ["10:00", "16:00"])   # None = no Saturdays

    @field_validator("weekday", "saturday")
    @classmethod
    def _span(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return None
        if len(v) != 2:
            raise ValueError("a sending window is [start, end], e.g. [\"09:00\", \"18:00\"]")
        start, end = _clamp_hhmm(v[0]), _clamp_hhmm(v[1])
        if start >= end:
            raise ValueError("the sending window must end after it starts")
        return [start, end]


class ReactivationSettings(BaseModel):
    """Dental patient recall: SMS to lapsed patients from the practice's own PMS (core/services/reactivation)."""

    model_config = ConfigDict(extra="ignore")
    enabled: bool = False
    recall_interval_months: int = Field(default=6, ge=3, le=24)
    template: str = ""                      # empty = DEFAULT_RECALL_TEMPLATE
    send_window: RecallSendWindow = Field(default_factory=RecallSendWindow)
    # Voice recall stays off: it needs express voice consent or a clean DNCR / TPS check per number.
    voice_enabled: bool = False

    @field_validator("template")
    @classmethod
    def _template(cls, v: str) -> str:
        v = " ".join((v or "").split())
        if v:
            problems = recall_template_problems(v)
            if problems:
                raise ValueError(" ".join(problems))
        return v

    @property
    def effective_template(self) -> str:
        return self.template or DEFAULT_RECALL_TEMPLATE


class PrivacySettings(BaseModel):
    """Health-data handling (UK GDPR Art. 9, Australian Privacy Principles). Dental practices always
    encrypt clinical free text; other businesses can opt in."""

    model_config = ConfigDict(extra="ignore")
    encrypt_clinical_text: bool = False
    lead_retention_days: int = Field(default=0, ge=0, le=3650)   # 0 = keep leads until deleted


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
    dental: DentalSettings = Field(default_factory=DentalSettings)
    missed_call_recovery: MissedCallRecovery = Field(default_factory=MissedCallRecovery)
    legal_entity: LegalEntity = Field(default_factory=LegalEntity)
    privacy: PrivacySettings = Field(default_factory=PrivacySettings)
    reactivation: ReactivationSettings = Field(default_factory=ReactivationSettings)
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
    def clinical_encryption(self) -> bool:
        """Store symptoms, treatment notes and call transcripts encrypted (core/sensitive.seal)."""
        return self.vertical == "dental" or self.privacy.encrypt_clinical_text

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

    def greeting_text(self, local_now: Optional[datetime] = None) -> str:
        """⚖ AI disclosure in the first sentence (EU AI Act, US state bot laws, UAE TDRA). ≤ 4 s.
        An owner's custom greeting that doesn't say it is an AI gets the disclosure added.
        Dental practices without a custom greeting get one for the moment of the call
        (`local_now`, business-local): lunch break, closed, or reception busy (overflow)."""
        custom = self.agent.greeting.strip()
        state = self.greeting_state(local_now) if (self.vertical == "dental" and not custom) else None
        if custom:
            text = custom if has_ai_disclosure(custom) else add_disclosure(custom, self.disclosure_sentence())
        elif state == "lunch_break":
            text = (f"Thanks for calling {self.name}. Our reception team is currently on a short lunch break, "
                    "so I'm their AI assistant and can book your appointment right now.")
        elif state == "after_hours":
            text = (f"Thanks for calling {self.name}. We're currently closed, but I'm the practice's AI assistant "
                    "and can book your consultation or take an urgent message for the team.")
        elif state == "busy_overflow":
            text = (f"Thanks for calling {self.name}. Our receptionists are helping other patients right now. "
                    "I'm the practice's AI assistant and can help get you booked in straight away.")
        else:
            text = (f"Thanks for calling {self.name}, this is {self.agent.name}, "
                    f"{self.name}'s virtual assistant.")
        rec = self.agent.recording
        if rec.enabled and rec.announce and "record" not in text.lower():
            if self.vertical == "dental":           # dental: the recording notice comes first
                text = (rec.announce_text or DENTAL_RECORDING_NOTICE) + " " + text
            else:
                text += " " + (rec.announce_text or DEFAULT_RECORDING_NOTICE)
        if "?" not in text:
            text += " How can I help?"
        return text

    def greeting_state(self, local_now: Optional[datetime]) -> Optional[str]:
        """lunch_break | after_hours | busy_overflow (open, calls forwarded when reception is busy) | None."""
        if local_now is None:
            return None
        window = self.answer_window(local_now)
        if window != "open":
            return window
        return "busy_overflow" if self.answering_mode == "overflow" else None

    def answer_window(self, local_now: datetime) -> AnswerWindow:
        """open, a short break between two of today's opening intervals, or closed."""
        if self.is_open(local_now):
            return "open"
        t = local_now.time()
        minutes = t.hour * 60 + t.minute
        today = [h for h in self.hours_for(local_now.weekday()) if not h.overnight]
        closed_since = [minutes - (h.close_time.hour * 60 + h.close_time.minute) for h in today if h.close_time <= t]
        opens_in = [(h.open_time.hour * 60 + h.open_time.minute) - minutes for h in today if h.open_time > t]
        if closed_since and opens_in and min(closed_since) + min(opens_in) <= MAX_BREAK_MINUTES:
            return "lunch_break"
        return "after_hours"

    def reopens_today(self, local_now: datetime) -> Optional[str]:
        """The next opening time today (HH:MM), if the business opens again later today."""
        t = local_now.time()
        later = sorted(h.open for h in self.hours_for(local_now.weekday()) if h.open_time > t)
        return later[0] if later else None

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
