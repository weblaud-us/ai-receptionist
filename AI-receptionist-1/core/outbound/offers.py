"""
Offers: what the sales dialer sells (SALES_DIALER_PLAN.md, Phase 1A).

An offer is everything the agent needs to sell one thing: the company it calls for, what it sells
and to whom, value and proof points, discovery and qualification questions, objection answers, the
price policy, the call's goal, the meeting it books, the emails it sends, and 1–4 script variants
(opener, first question, pitch) for A/B testing. Offers are written in the platform dashboard; no
deploy is needed to sell something new.

Two collections:
    sales_offers           the editable draft, one per offer
    sales_offer_versions   immutable published snapshots, `_id = "<offer id>@<n>"`

A campaign pins one published version. Publishing a new version never changes what a running
campaign says; moving a campaign to it is a material change, so it needs a fresh legal review
(models.MATERIAL_FIELDS). Every attempt records the offer, version and variant it used.

Safety, whatever an admin types:
- Placeholders come from a per-field whitelist and are filled by `render` (a regex), never
  `str.format`, so "{business.__class__}"-style tricks are impossible.
- Every opener must say it is an AI, name the company and offer an opt-out (`check_opener`).
- Tags that look like the prompt's data fences (<offer>, <prospect_research>…) are stripped, so
  offer text can't close a fence and pose as rules.
- The agent's rules (honesty, opt-out, no pressure, email only) are fixed in persona.py; an offer
  only supplies information, inside an <offer> fence.

There is no delete: offers are archived, and old versions stay so every call can be audited to the
exact text it used.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from config import settings

OFFERS = "sales_offers"
OFFER_VERSIONS = "sales_offer_versions"
TEST_CALLS = "sales_test_calls"             # browser test calls of a draft (agent/sales.py test_call)
TEST_CALL_MAX_S = 300
LEGACY_OFFER_ID = "off_laudline"            # campaigns from before offers existed sell this (version 0, in code)
OFFER_STATUSES: tuple[str, ...] = ("draft", "active", "archived")
MAX_OFFER_CHARS = 7000                      # keeps the prompt short enough for fast first replies

# ── Templating ───────────────────────────────────────────────────────────────

PLACEHOLDERS: Mapping[str, frozenset[str]] = {
    "opener": frozenset({"persona", "company", "business", "contact"}),
    "prompt": frozenset({"persona", "company", "business", "contact"}),
    "voicemail": frozenset({"persona", "company", "callback"}),
    "email": frozenset({"name", "first_name", "business", "company", "persona", "meeting_label", "meeting_url",
                        "when", "host", "website"}),
    "consent": frozenset({"company"}),
}
_TOKEN = re.compile(r"\{([^{}]*)\}")
_NAME = re.compile(r"^[a-z_]+$")
FENCE_TAG = re.compile(r"</?\s*(?:offer|prospect_research|campaign_notes|lead_notes|earlier_calls|playbook|"
                       r"website_text|rules)\b[^>]*>", re.IGNORECASE)
_URL = re.compile(r"https?://|www\.", re.IGNORECASE)
_OPT_OUT = ("off our list", "off the list", "opt out", "not to be called", "stop calling", "rather not")
# The opener must say the *caller* is an AI, not merely mention AI ("we build AI tools" is not a disclosure).
_AI_IDENTITY = re.compile(
    r"\b(i'?m|i am)\s+(an?\s+|the\s+)?(ai|artificial[- ]intelligence)\b"
    r"|\b(an?|the)\s+(ai|artificial[- ]intelligence)\s+(assistant|agent|voice|caller|receptionist|system|model|bot)\b"
    r"|\b(an?|the)\s+ai\b(?=\s*(,|\.|;|$|from\b|calling\b|on\s+behalf\b|here\b|working\b))",
    re.IGNORECASE)


def render(template: str, values: Mapping[str, Any]) -> str:
    """Fill {name} placeholders. Anything that isn't a plain known name is dropped, never evaluated."""
    def sub(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(values.get(name, "")) if _NAME.match(name) else ""
    return _TOKEN.sub(sub, template or "")


def placeholder_problems(label: str, text: str, kind: str) -> List[str]:
    allowed = PLACEHOLDERS[kind]
    problems = []
    for token in _TOKEN.findall(text or ""):
        if not _NAME.match(token) or token not in allowed:
            problems.append(f"{label}: {{{token}}} is not allowed here (use {', '.join('{' + a + '}' for a in sorted(allowed))})")
    if text and (text.count("{") != text.count("}")):
        problems.append(f"{label}: unbalanced braces")
    return problems


def scrub(value: Any) -> Any:
    """Strip fence-like tags from every string in an offer."""
    if isinstance(value, str):
        return FENCE_TAG.sub("", value).strip()
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    return value


def check_opener(text: str, *, company: Optional[str] = None) -> List[str]:
    """What an AI opener must contain. Empty list = fine."""
    company = company if company is not None else settings.SALES_COMPANY_NAME
    low = (text or "").lower()
    problems = []
    if not _AI_IDENTITY.search(" ".join(low.replace("’", "'").split())):
        problems.append("does not say it is an AI (e.g. \"this is Ava, an AI assistant from …\")")
    if not company or company.lower() not in low:
        problems.append("does not name the company")
    if not any(p in low for p in _OPT_OUT):
        problems.append("does not offer a way to opt out (e.g. \"…just say so and I'll take you off our list\")")
    if "recorded" in low:
        problems.append("mentions recording (calls are never recorded)")
    if "real person" in low or "i'm human" in low or "i am human" in low:
        problems.append("claims to be a person")
    if len(text or "") > 360:
        problems.append("is too long to say in one breath (360 characters at most)")
    return problems


# ── The shape of an offer ────────────────────────────────────────────────────

_Item = Field(max_length=240)


class Objection(BaseModel):
    model_config = ConfigDict(extra="ignore")
    objection: str = Field(min_length=2, max_length=120)
    response: str = Field(min_length=2, max_length=400)


class ScriptVariant(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(pattern=r"^[a-z0-9_]{2,32}$")
    label: str = Field(min_length=2, max_length=60)
    cold_opener: str = Field(min_length=10, max_length=360)          # spoken word for word by the AI
    first_question: str = Field(default="", max_length=200)
    pitch: str = Field(default="", max_length=400)
    human_opener: str = Field(default="", max_length=360)            # cheat sheet for a person (human-assisted)


class Pricing(BaseModel):
    model_config = ConfigDict(extra="ignore")
    mode: Literal["never", "range"] = "never"
    text: str = Field(default="", max_length=200)                   # the exact sentence it may say


class Meeting(BaseModel):
    model_config = ConfigDict(extra="ignore")
    label: str = Field(default="demo", min_length=2, max_length=40)  # "demo", "discovery call"…
    length_min: int = Field(default=15, ge=10, le=60)
    service_name: str = Field(default_factory=lambda: settings.SALES_DEMO_SERVICE, max_length=60)
    url: str = Field(default="", max_length=300)                     # video link in the confirmation email
    host_name: str = Field(default="", max_length=60)                # who they meet; default SALES_FOUNDER_NAME


class RoiInput(BaseModel):
    """A number the agent asks the prospect for ("Roughly how many calls go unanswered in a week?")."""

    model_config = ConfigDict(extra="ignore")
    question: str = Field(min_length=5, max_length=160)            # asked out loud, in the agent's words
    label: str = Field(min_length=2, max_length=40)                 # "missed calls a week"
    per: Literal["week", "month", "once"] = "once"                  # week/month = a rate; once = a plain amount
    max: float = Field(default=1_000_000, gt=0, le=1_000_000_000)   # anything bigger is a mishearing


class RoiFactor(BaseModel):
    """One of the seller's own figures ("share of those calls we answer: 80 %"). It's a claim, like a proof point."""

    model_config = ConfigDict(extra="ignore")
    label: str = Field(min_length=2, max_length=60)
    value: float = Field(gt=0, le=1_000_000_000)
    percent: bool = False                                            # 80 means 80 %


class Roi(BaseModel):
    """What the offer could be worth to them: their numbers × your figures, worked out by the
    estimate_roi tool (never by the model) and said as a rough estimate."""

    model_config = ConfigDict(extra="ignore")
    enabled: bool = False
    inputs: List[RoiInput] = Field(default_factory=list, max_length=4)
    factors: List[RoiFactor] = Field(default_factory=list, max_length=4)
    result_label: str = Field(default="extra revenue a month", max_length=60)
    monthly_cost: Optional[float] = Field(default=None, ge=0, le=100_000_000)  # for "about N times what it costs"


class FollowUpChannels(BaseModel):
    """Where details go when they won't book on the call: email (always), WhatsApp, or a page they book on."""

    model_config = ConfigDict(extra="ignore")
    whatsapp: bool = False
    whatsapp_template: str = Field(default="", max_length=80, pattern=r"^[a-z0-9_]*$")   # Meta-approved template
    whatsapp_language: str = Field(default="en", max_length=10, pattern=r"^[a-z]{2}(_[A-Z]{2})?$")
    booking_url: str = Field(default="", max_length=200)             # said out loud as a last resort


class EmailTemplate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    subject: str = Field(min_length=2, max_length=150)
    body: str = Field(min_length=10, max_length=4000)


class FollowUp(BaseModel):
    """One email of a follow-up sequence (SALES_SUITE_PLAN.md Phase 17): sent N days after the trigger,
    only to someone who agreed to email."""

    model_config = ConfigDict(extra="ignore")
    after_days: int = Field(default=2, ge=1, le=30)
    subject: str = Field(min_length=2, max_length=150)
    body: str = Field(min_length=10, max_length=4000)


def _default_info_email() -> EmailTemplate:
    return EmailTemplate(subject="{company}: a quick note for {business}",
                         body="Hi {first_name},\n\nThanks for taking our call. Reply to this email and we'll set up "
                              "a short {meeting_label} at a time that suits you.")


def _default_confirmation_email() -> EmailTemplate:
    return EmailTemplate(subject="Your {company} {meeting_label}: {when}",
                         body="Hi {first_name},\n\nYour {meeting_label} with {host} is booked for {when}.\n"
                              "{meeting_url}\n\nReply to this email if you need to change the time.")


class OfferSettings(BaseModel):
    """The editable part of an offer."""

    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=2, max_length=80)
    brand: Literal["laudline", "weblaud", "other"] = "other"
    company_name: str = Field(min_length=2, max_length=60)          # the name the agent says on the call
    persona_name: str = Field(default_factory=lambda: settings.SALES_PERSONA_NAME, min_length=2, max_length=30)
    website: str = Field(default="", max_length=200)
    what_we_sell: str = Field(min_length=10, max_length=600)
    ideal_customer: str = Field(default="", max_length=400)
    value_props: List[str] = Field(default_factory=list, max_length=6)
    proof_points: List[str] = Field(default_factory=list, max_length=6)     # the only claims the agent may make
    discovery_questions: List[str] = Field(default_factory=list, max_length=6)
    qualification_questions: List[str] = Field(default_factory=list, max_length=6)
    pain_hypotheses: List[str] = Field(default_factory=list, max_length=6)
    objections: List[Objection] = Field(default_factory=list, max_length=12)
    pricing: Pricing = Field(default_factory=Pricing)
    roi: Roi = Field(default_factory=Roi)
    call_goal: Literal["book_meeting", "send_info", "transfer"] = "book_meeting"
    meeting: Meeting = Field(default_factory=Meeting)
    channels: FollowUpChannels = Field(default_factory=FollowUpChannels)
    info_email: EmailTemplate = Field(default_factory=_default_info_email)
    confirmation_email: EmailTemplate = Field(default_factory=_default_confirmation_email)
    consented_opener: str = Field(default="", max_length=360)       # for people who asked for the call
    callback_opener: str = Field(default="", max_length=360)        # when they asked us to call back (optional)
    voicemail: str = Field(default="", max_length=300)              # consented campaigns only
    consent_text: str = Field(default="", max_length=1000)          # shown next to the "call me" checkbox
    variants: List[ScriptVariant] = Field(min_length=1, max_length=4)
    follow_ups: List[FollowUp] = Field(default_factory=list, max_length=3)  # emails after "send me info" / a booking
    deal_value_default: Optional[float] = Field(default=None, ge=0, le=100_000_000)  # a new deal's amount
    deal_currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")

    @model_validator(mode="before")
    @classmethod
    def _scrub(cls, data: Any) -> Any:
        return scrub(data) if isinstance(data, dict) else data

    @field_validator("value_props", "proof_points", "discovery_questions", "qualification_questions",
                     "pain_hypotheses")
    @classmethod
    def _items(cls, v: List[str]) -> List[str]:
        out = [s.strip() for s in v if s and s.strip()]
        if any(len(s) > 240 for s in out):
            raise ValueError("each item is 240 characters at most")
        return out

    @field_validator("variants")
    @classmethod
    def _unique(cls, v: List[ScriptVariant]) -> List[ScriptVariant]:
        ids = [x.id for x in v]
        if len(ids) != len(set(ids)):
            raise ValueError("script variant ids must be unique")
        return v


SAMPLE_LEAD: Mapping[str, Any] = {"business_name": "Harbour Dental Studio", "contact_name": "Alex"}


def opener_values(offer: Mapping[str, Any], lead: Mapping[str, Any]) -> Dict[str, str]:
    return {"persona": str(offer.get("persona_name") or settings.SALES_PERSONA_NAME),
            "company": str(offer.get("company_name") or settings.SALES_COMPANY_NAME),
            "business": str(lead.get("business_name") or "").strip() or "your business",
            "contact": str(lead.get("contact_name") or "").strip() or "the owner"}


_NOT_IN_PROMPT = {"info_email", "confirmation_email", "consent_text", "voicemail", "consented_opener",
                  "callback_opener", "website",
                  "brand", "name", "follow_ups", "deal_value_default", "deal_currency", "channels"}


# ── Saying numbers and web addresses ─────────────────────────────────────────

_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
         "seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _words_under_1000(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    parts = [f"{_ONES[hundreds]} hundred"] if hundreds else []
    if rest >= 20:
        parts.append(_TENS[rest // 10] + (f"-{_ONES[rest % 10]}" if rest % 10 else ""))
    elif rest or not parts:
        parts.append(_ONES[rest])
    return " and ".join(parts) if len(parts) == 2 and hundreds else " ".join(parts)


def number_words(n: float) -> str:
    """A whole number as it is said: 4200 → "four thousand two hundred", 1_200_000 → "one point two million"."""
    n = int(round(n))
    if n < 0:
        return "minus " + number_words(-n)
    if n >= 1_000_000:
        millions = n / 1_000_000
        whole, tenth = int(millions), int(round((millions - int(millions)) * 10))
        if tenth == 10:
            whole, tenth = whole + 1, 0
        return number_words(whole) + (f" point {_ONES[tenth]}" if tenth else "") + " million"
    thousands, rest = divmod(n, 1000)
    parts = ([_words_under_1000(thousands) + " thousand"] if thousands else []) + ([_words_under_1000(rest)] if rest or not thousands else [])
    return " ".join(parts)


def round_figure(x: float) -> float:
    """Two significant figures: 4,237 → 4,200. An estimate shouldn't sound precise."""
    if x <= 0:
        return 0.0
    digits = len(str(int(x))) - 2
    return float(round(x, -digits)) if digits > 0 else float(round(x))


_CURRENCY_WORDS = {"USD": "dollars", "AUD": "dollars", "CAD": "dollars", "GBP": "pounds", "EUR": "euros"}


def spoken_amount(x: float, currency: str) -> str:
    return f"{number_words(round_figure(x))} {_CURRENCY_WORDS.get(currency.upper(), currency.upper())}"


def spoken_url(url: str) -> str:
    """How a web address is said: "https://www.weblaud.com/book" → "weblaud dot com slash book"."""
    s = re.sub(r"^https?://", "", str(url or "").strip(), flags=re.I)
    s = re.sub(r"^www\.", "", s, flags=re.I).split("?")[0].split("#")[0].rstrip("/")
    return s.lower().replace(".", " dot ").replace("/", " slash ").replace("-", " dash ").strip()


def booking_url(offer: Mapping[str, Any]) -> str:
    """The page they can book on themselves: the offer's own, else its website."""
    channels = offer.get("channels") or {}
    return str(channels.get("booking_url") or offer.get("website") or "").strip()


# ── ROI: their numbers × your figures ────────────────────────────────────────

WEEKS_PER_MONTH = 52 / 12


def roi_estimate(roi: Mapping[str, Any], answers: Sequence[Any]) -> Dict[str, Any]:
    """Work out the estimate from the prospect's answers (in the order of `roi.inputs`).
    {"amount": float, "monthly": bool} or {"error", "missing": index} / {"error", "index"} for a bad answer."""
    inputs = list(roi.get("inputs") or [])
    values: List[float] = []
    for i, spec in enumerate(inputs):
        raw = answers[i] if i < len(answers) else None
        try:
            x = float(raw) if raw is not None and str(raw).strip() != "" else None
        except (TypeError, ValueError):
            x = None
        if x is None or x < 0:
            return {"error": "missing", "index": i}
        if x > float(spec.get("max") or 1_000_000):
            return {"error": "too_big", "index": i}
        values.append(x * (WEEKS_PER_MONTH if spec.get("per") == "week" else 1.0))
    amount = 1.0
    for x in values:
        amount *= x
    for f in roi.get("factors") or []:
        amount *= float(f.get("value") or 0) / (100.0 if f.get("percent") else 1.0)
    return {"amount": amount, "monthly": any(s.get("per") in ("week", "month") for s in inputs)}


def text_size(s: OfferSettings) -> int:
    """Roughly how much of the agent's prompt the offer takes (emails and consent wording aren't in it)."""
    body = s.model_dump(exclude=_NOT_IN_PROMPT)
    for v in body.get("variants") or []:
        v.pop("human_opener", None)
    return len(json.dumps(body, ensure_ascii=False))


class Issue(BaseModel):
    """One thing to fix (a problem stops publishing) or worth a look (a warning), tied to the field it's
    about ("variants.1.cold_opener") so the editor can show it on the right tab and jump to it."""

    level: Literal["problem", "warning"]
    field: str
    message: str


class _Issues:
    def __init__(self) -> None:
        self.items: List[Issue] = []

    def problem(self, field: str, *messages: str) -> None:
        self.items += [Issue(level="problem", field=field, message=m) for m in messages]

    def warn(self, field: str, *messages: str) -> None:
        self.items += [Issue(level="warning", field=field, message=m) for m in messages]

    def done(self) -> List[Issue]:
        seen: set[tuple[str, str]] = set()
        out = []
        for i in self.items:
            if (i.level, i.message) not in seen:
                seen.add((i.level, i.message))
                out.append(i)
        return out


_SMS = re.compile(r"\b(sms|text message|texts? you|we'?ll text|send you a text)\b", re.IGNORECASE)
_FREE_TRIAL = re.compile(r"\bfree\s+trial\b", re.IGNORECASE)


def _spoken_texts(s: OfferSettings) -> Iterable[Tuple[str, str]]:
    """(field, text) for everything the agent says or the prospect reads."""
    yield "what_we_sell", s.what_we_sell
    for i, v in enumerate(s.variants):
        for key in ("cold_opener", "first_question", "pitch", "human_opener"):
            yield f"variants.{i}.{key}", getattr(v, key)
    for i, o in enumerate(s.objections):
        yield f"objections.{i}.response", o.response
    for i, text in enumerate(s.value_props):
        yield f"value_props.{i}", text
    yield "consented_opener", s.consented_opener
    yield "callback_opener", s.callback_opener
    yield "voicemail", s.voicemail
    yield "info_email.body", s.info_email.body
    yield "confirmation_email.body", s.confirmation_email.body
    yield "pricing.text", s.pricing.text


def validate_issues(s: OfferSettings) -> List[Issue]:
    """Every problem (stops publishing) and warning, each tied to its field."""
    out = _Issues()
    values = opener_values(s.model_dump(), SAMPLE_LEAD)

    for i, v in enumerate(s.variants):
        f = f"variants.{i}"
        out.problem(f"{f}.cold_opener", *placeholder_problems(f"Variant {v.label}: opener", v.cold_opener, "opener"))
        out.problem(f"{f}.human_opener",
                    *placeholder_problems(f"Variant {v.label}: human opener", v.human_opener, "opener"))
        for key, label, text in (("first_question", "first question", v.first_question), ("pitch", "pitch", v.pitch)):
            out.problem(f"{f}.{key}", *placeholder_problems(f"Variant {v.label}: {label}", text, "prompt"))
        out.problem(f"{f}.cold_opener", *[f"Variant {v.label}: the opener {p}"
                                          for p in check_opener(render(v.cold_opener, values), company=s.company_name)])
        for key, label, text in (("cold_opener", "opener", v.cold_opener), ("pitch", "pitch", v.pitch),
                                 ("first_question", "first question", v.first_question)):
            if _URL.search(text):
                out.warn(f"{f}.{key}", f"Variant {v.label}: the {label} has a web address, which sounds odd read aloud")
        if v.human_opener and s.company_name.lower() not in render(v.human_opener, values).lower():
            out.warn(f"{f}.human_opener", f"Variant {v.label}: the human opener doesn't name {s.company_name}")

    if s.consented_opener:
        out.problem("consented_opener", *placeholder_problems("Consented opener", s.consented_opener, "opener"))
        out.problem("consented_opener", *[f"Consented opener {p}" for p in check_opener(
            render(s.consented_opener, values), company=s.company_name)])
    else:
        out.warn("consented_opener", "No opener for people who asked for a call: consented campaigns can't use this offer")
    if s.callback_opener:                          # optional: without one, a callback hears the usual opener
        out.problem("callback_opener", *placeholder_problems("Callback opener", s.callback_opener, "opener"))
        out.problem("callback_opener", *[f"Callback opener {p}" for p in check_opener(
            render(s.callback_opener, values), company=s.company_name)])
    if s.voicemail:
        out.problem("voicemail", *placeholder_problems("Voicemail", s.voicemail, "voicemail"))
        low = render(s.voicemail, {**values, "callback": "{callback}"}).lower()
        if " ai " not in f" {low} " and "an ai" not in low:
            out.problem("voicemail", "Voicemail: must say it is an AI")
        if s.company_name.lower() not in low:
            out.problem("voicemail", "Voicemail: must name the company")
        if "{callback}" not in s.voicemail:
            out.problem("voicemail", "Voicemail: must give the {callback} number so they can opt out")
    for key, label, tmpl in (("info_email", "Information email", s.info_email),
                             ("confirmation_email", "Confirmation email", s.confirmation_email)):
        out.problem(f"{key}.subject", *placeholder_problems(f"{label} subject", tmpl.subject, "email"))
        out.problem(f"{key}.body", *placeholder_problems(f"{label} body", tmpl.body, "email"))
    out.problem("consent_text", *placeholder_problems("Consent text", s.consent_text, "consent"))
    for i, step in enumerate(s.follow_ups):
        out.problem(f"follow_ups.{i}.subject", *placeholder_problems(f"Follow-up {i + 1} subject", step.subject, "email"))
        out.problem(f"follow_ups.{i}.body", *placeholder_problems(f"Follow-up {i + 1} body", step.body, "email"))
    if [st.after_days for st in s.follow_ups] != sorted(st.after_days for st in s.follow_ups):
        out.warn("follow_ups", "Follow-up emails go out in the order of their days: put them in that order")
    for key, label, items in (("what_we_sell", "What we sell", [s.what_we_sell]),
                              ("ideal_customer", "Ideal customer", [s.ideal_customer]),
                              ("value_props", "Value points", s.value_props),
                              ("proof_points", "Proof points", s.proof_points),
                              ("discovery_questions", "Discovery questions", s.discovery_questions),
                              ("qualification_questions", "Qualification questions", s.qualification_questions),
                              ("pain_hypotheses", "Likely pains", s.pain_hypotheses),
                              ("objections", "Objections", [f"{o.objection} {o.response}" for o in s.objections])):
        for n, item in enumerate(items):
            field = key if key in ("what_we_sell", "ideal_customer") else f"{key}.{n}"
            out.problem(field, *placeholder_problems(label, item, "prompt"))
    if s.pricing.mode == "range" and not s.pricing.text.strip():
        out.problem("pricing.text", "Pricing: write the exact sentence the agent may say about price")
    if s.call_goal == "book_meeting" and not s.meeting.service_name:
        out.problem("meeting.service_name", "Meeting: name the service on the sales calendar that meetings are booked as")
    if not any(re.search(r"\babout\b|\bregarding\b|\bbecause\b", v.cold_opener, re.IGNORECASE) for v in s.variants):
        # ⚠ VERIFY: the AU Telemarketing Industry Standard expects the purpose of the call early on.
        out.warn("variants.0.cold_opener", "No opener says why you're calling (e.g. \"…calling from {company} about …\"). "
                                           "For Australian calls, say the purpose early.")
    if not s.proof_points:
        out.warn("proof_points", "No proof points: the agent won't be able to back up any claim")
    if not s.discovery_questions:
        out.warn("discovery_questions", "No discovery questions: the agent will pitch before it understands their problem")
    for field, text in _spoken_texts(s):
        if text and _SMS.search(text):
            out.warn(field, "Mentions texting or SMS: sales follow-up is by email (or WhatsApp when it is switched on), never SMS")
        if text and s.brand == "laudline" and _FREE_TRIAL.search(text):
            out.warn(field, "LaudLine offers a \"14-day pilot\", not a \"free trial\"")
    if s.roi.enabled:
        if not s.roi.inputs:
            out.problem("roi.inputs", "ROI: add at least one question the agent asks them")
        if sum(1 for x in s.roi.inputs if x.per in ("week", "month")) > 1:
            out.problem("roi.inputs", "ROI: only one question can be \"per week\" or \"per month\"; make the others \"a plain number\"")
        for i, x in enumerate(s.roi.inputs):
            out.problem(f"roi.inputs.{i}.question", *placeholder_problems(f"ROI question {i + 1}", x.question, "prompt"))
        if not s.roi.result_label.strip():
            out.problem("roi.result_label", "ROI: say what the result is, e.g. \"extra revenue a month\"")
        if s.roi.monthly_cost and s.pricing.mode != "range":
            out.warn("roi.monthly_cost", "ROI: the monthly cost is only used when the agent may mention a price (Price tab)")
        if not s.roi.factors:
            out.warn("roi.factors", "ROI: no figures of your own, so the estimate is only their numbers multiplied")
    if s.channels.whatsapp and not s.channels.whatsapp_template:
        out.problem("channels.whatsapp_template", "WhatsApp: name the approved Meta template to send")
    url = s.channels.booking_url
    if url and not re.match(r"^(https?://)?[a-z0-9.-]+\.[a-z]{2,}(/\S*)?$", url, re.IGNORECASE):
        out.problem("channels.booking_url", "Booking page: that doesn't look like a web address (e.g. weblaud.com/book)")
    if url and len(spoken_url(url).split()) > 9:
        out.warn("channels.booking_url", "Booking page: that address is long to say out loud; use a short one")
    size = text_size(s)
    if size > MAX_OFFER_CHARS:
        out.problem("offer", f"The offer is {size} characters; keep it under {MAX_OFFER_CHARS} so the agent stays quick")
    return out.done()


def validate_offer(s: OfferSettings) -> Tuple[List[str], List[str]]:
    """(problems that stop publishing, warnings worth a look)."""
    issues = validate_issues(s)
    return ([i.message for i in issues if i.level == "problem"], [i.message for i in issues if i.level == "warning"])


# AI-written facts a person must confirm before publishing (SALES_SUITE_PLAN.md Phase 1): proof points
# are the only claims the agent may make; the price sentence is said word for word; consent is legal text.
AI_FACT_FIELDS = re.compile(r"^(proof_points\.\d+|roi\.factors\.\d+|pricing\.text|consent_text)$")


def fact_label(path: str) -> str:
    if path.startswith("proof_points."):
        return f"Proof point {int(path.split('.')[1]) + 1}"
    if path.startswith("roi.factors."):
        return f"ROI figure {int(path.split('.')[2]) + 1}"
    return {"pricing.text": "The price sentence", "consent_text": "The consent wording"}.get(path, path)


def clean_unconfirmed(paths: Iterable[str], s: Optional[OfferSettings] = None) -> List[str]:
    """Only real fact paths, each once, and only those that still exist in the offer."""
    out: List[str] = []
    for path in paths:
        path = str(path)
        if not AI_FACT_FIELDS.match(path) or path in out:
            continue
        if s is not None:
            if path.startswith("proof_points.") and int(path.split(".")[1]) >= len(s.proof_points):
                continue
            if path.startswith("roi.factors.") and int(path.split(".")[2]) >= len(s.roi.factors):
                continue
            if path == "pricing.text" and not s.pricing.text.strip():
                continue
            if path == "consent_text" and not s.consent_text.strip():
                continue
        out.append(path)
    return out[:20]


def fact_texts(s: OfferSettings) -> Dict[str, str]:
    """Every fact field the offer has now, by path, with the text a person has to vouch for."""
    out = {f"proof_points.{i}": p for i, p in enumerate(s.proof_points)}
    out.update({f"roi.factors.{i}": json.dumps(f.model_dump(), sort_keys=True) for i, f in enumerate(s.roi.factors)})
    if s.pricing.text.strip():
        out["pricing.text"] = s.pricing.text
    if s.consent_text.strip():
        out["consent_text"] = s.consent_text
    return out


def fact_hash(text: str) -> str:
    return hashlib.sha256(" ".join(str(text).split()).lower().encode("utf-8")).hexdigest()[:20]


def track_unconfirmed(s: OfferSettings, flagged: Iterable[str], *, previous: Iterable[str] = (),
                      confirmed: Iterable[str] = ()) -> Tuple[List[str], List[str]]:
    """(unconfirmed paths, their text hashes) after a save.

    `previous` are the hashes of AI-written texts nobody has confirmed yet: such a text stays
    unconfirmed wherever it now sits (an earlier item was deleted, the list was reordered), even
    if the editor's paths didn't follow it. Only an explicit `confirmed` path clears one. `flagged`
    (paths the editor marks as AI-written) can only add."""
    texts = fact_texts(s)
    keep = set(previous)
    cleared = {p for p in confirmed if p in texts}
    out = set(clean_unconfirmed(flagged, s)) - cleared
    out |= {p for p, t in texts.items() if fact_hash(t) in keep and p not in cleared}
    paths = [p for p in texts if p in out][:20]
    return paths, sorted({fact_hash(texts[p]) for p in paths})


def unconfirmed_issues(paths: Iterable[str], s: Optional[OfferSettings] = None) -> List[Issue]:
    """One problem per AI-written fact nobody has confirmed yet."""
    return [Issue(level="problem", field=path,
                  message=f"{fact_label(path)} was written by AI: check it's true and tick Confirm")
            for path in clean_unconfirmed(paths, s)]


# The editor's names for fields, so an unfinished draft gets a checklist rather than validator output.
_FIELD_LABELS = {
    "name": "Offer name", "company_name": "Company the agent calls for", "persona_name": "Agent's name",
    "website": "Website", "what_we_sell": "What you sell", "ideal_customer": "Who it's for",
    "value_props": "Value points", "proof_points": "Proof points", "discovery_questions": "Discovery questions",
    "qualification_questions": "Qualification", "pain_hypotheses": "Likely problems", "objections": "Objections",
    "pricing": "Price", "meeting": "Meeting", "info_email": "Information email",
    "confirmation_email": "Meeting confirmation", "consented_opener": "Opener for people who asked for a call",
    "callback_opener": "Opener when they asked us to call back",
    "voicemail": "Voicemail", "consent_text": "Consent wording", "variants": "Scripts",
    "follow_ups": "Follow-up emails", "deal_value_default": "Deal value", "deal_currency": "Currency",
    "roi": "ROI", "channels": "Follow-up channels",
}
_ITEM_LABELS = {"variants": "Script", "objections": "Objection", "follow_ups": "Follow-up", "value_props": "Value point",
                "proof_points": "Proof point", "discovery_questions": "Discovery question",
                "qualification_questions": "Qualification question", "pain_hypotheses": "Likely problem"}
_PART_LABELS = {"label": "name", "cold_opener": "AI opener", "first_question": "first question",
                "human_opener": "opener for a person", "response": "answer", "text": "price sentence",
                "length_min": "minutes", "service_name": "booked as", "url": "video link", "host_name": "who they meet",
                "inputs": "questions", "factors": "your figures", "question": "question", "value": "number",
                "result_label": "what the result is", "monthly_cost": "monthly cost",
                "whatsapp_template": "WhatsApp template", "whatsapp_language": "template language",
                "booking_url": "booking page"}


def _loc_path(loc: Sequence[Any]) -> str:
    return ".".join(str(x) for x in loc) or "offer"


def draft_issues(err: ValidationError) -> List[Issue]:
    """Why an unfinished draft isn't an offer yet, in the editor's words ("What you sell needs at least…"),
    each tied to the field it's about."""
    out: List[Issue] = []
    seen: set[str] = set()
    for e in err.errors():
        loc = list(e.get("loc") or ())
        path = _loc_path(loc)
        field = str(loc.pop(0)) if loc else ""
        where = _FIELD_LABELS.get(field, field.replace("_", " ").capitalize() or "Offer")
        if loc and isinstance(loc[0], int):
            where = f"{_ITEM_LABELS.get(field, where)} {int(loc.pop(0)) + 1}"
        if loc:
            where += ": " + _PART_LABELS.get(str(loc[0]), str(loc[0]).replace("_", " "))
        ctx = e.get("ctx") or {}
        kind = str(e.get("type") or "")
        unit = " characters" if kind.startswith("string_") else ""
        if kind == "missing" or (kind == "string_too_short" and not e.get("input")):
            msg = "is required"
        elif kind in ("string_too_short", "too_short"):
            msg = f"needs at least {ctx.get('min_length')}{unit}"
        elif kind in ("string_too_long", "too_long"):
            msg = f"is too long ({ctx.get('max_length')}{unit} at most)"
        else:
            detail = str(e.get("msg") or "").removeprefix("Value error, ")
            msg = f"is not valid ({detail[:1].lower() + detail[1:]})" if detail else "is not valid"
        text = f"{where} {msg}"
        if text not in seen:
            seen.add(text)
            out.append(Issue(level="problem", field=path, message=text))
    return out


def draft_problems(err: ValidationError) -> List[str]:
    return [i.message for i in draft_issues(err)]


def checksum(settings_doc: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(settings_doc, sort_keys=True, default=str).encode("utf-8")).hexdigest()


# ── At call time ─────────────────────────────────────────────────────────────

def runtime(settings_doc: Mapping[str, Any], offer_id: str, version: int) -> Dict[str, Any]:
    """The plain dict the prompt builder and tools read: the settings plus id and version."""
    return {**OfferSettings.model_validate(dict(settings_doc)).model_dump(), "id": offer_id, "version": version}


def variant(offer: Mapping[str, Any], variant_id: Optional[str]) -> Dict[str, Any]:
    variants = list(offer.get("variants") or [])
    return next((v for v in variants if v["id"] == variant_id), variants[0])


def pick_variant(variant_ids: Sequence[str], lead_id: str) -> Optional[str]:
    """Stable split: the same lead always gets the same variant."""
    ids = [v for v in variant_ids if v]
    if not ids:
        return None
    digest = hashlib.sha256(lead_id.encode("utf-8")).digest()
    return ids[digest[0] % len(ids)]


async def load_version(db: Any, offer_id: str, version: Optional[int]) -> Optional[Dict[str, Any]]:
    if not offer_id or not version:
        return None
    doc = await db[OFFER_VERSIONS].find_one({"_id": f"{offer_id}@{int(version)}"})
    return runtime(doc["settings"], offer_id, int(version)) if doc else None


async def offer_for(db: Any, offer_id: Optional[str], version: Optional[int]) -> Dict[str, Any]:
    """The offer a campaign or attempt names; campaigns from before offers sell the built-in LaudLine one."""
    if offer_id and version:
        found = await load_version(db, offer_id, version)
        if found is not None:
            return found
    from core.outbound.persona import legacy_offer

    return legacy_offer()


async def offer_for_campaign(db: Any, campaign: Mapping[str, Any]) -> Dict[str, Any]:
    return await offer_for(db, campaign.get("offer_id"), campaign.get("offer_version"))


def offer_id_of(campaign_or_attempt: Mapping[str, Any]) -> str:
    return str(campaign_or_attempt.get("offer_id") or LEGACY_OFFER_ID)


# ── Storage ──────────────────────────────────────────────────────────────────

class OfferError(Exception):
    def __init__(self, message: str, problems: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.problems = list(problems)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StaleDraft(OfferError):
    """Someone saved the draft after this editor loaded it."""

    def __init__(self, updated_at: Any, by: str = "") -> None:
        super().__init__("stale_draft")
        self.updated_at = updated_at
        self.by = by


def _aware(value: Any) -> Optional[datetime]:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def same_moment(a: Any, b: Any) -> bool:
    """Mongo keeps milliseconds; a browser echoes what it was sent. Compare to the millisecond."""
    x, y = _aware(a), _aware(b)
    return x is not None and y is not None and abs((x - y).total_seconds()) < 0.001


def changed_keys(before: Mapping[str, Any], after: OfferSettings) -> List[str]:
    """The settings a save changed, for the audit trail."""
    new = after.model_dump()
    return [k for k in OfferSettings.model_fields if before.get(k) != new.get(k)]


async def create_offer(db: Any, s: OfferSettings, *, by: str, offer_id: str, now: Optional[datetime] = None,
                       ai_unconfirmed: Iterable[str] = ()) -> Dict[str, Any]:
    now = now or _utcnow()
    paths, hashes = track_unconfirmed(s, ai_unconfirmed)
    doc = {"_id": offer_id, **s.model_dump(), "status": "draft", "published_version": None,
           "ai_unconfirmed": paths, "ai_fact_hashes": hashes,
           "created_by": by, "created_at": now, "updated_at": now, "updated_by": by}
    await db[OFFERS].insert_one(doc)
    return doc


async def update_offer(db: Any, offer_id: str, s: OfferSettings, *, now: Optional[datetime] = None,
                       by: str = "", ai_unconfirmed: Optional[Iterable[str]] = None,
                       ai_confirmed: Iterable[str] = (), reset_facts: bool = False,
                       expected_updated_at: Any = None) -> Optional[Dict[str, Any]]:
    """Save the draft. With `expected_updated_at`, refuse (StaleDraft) when someone saved it since.
    AI-written facts stay unconfirmed until a person confirms each one (`ai_confirmed`); see
    track_unconfirmed. `reset_facts` (restoring a published version, whose facts were confirmed)
    clears them."""
    now = now or _utcnow()
    query: Dict[str, Any] = {"_id": offer_id}
    current = await db[OFFERS].find_one({"_id": offer_id})
    if current is None:
        return None
    if expected_updated_at is not None:
        if not same_moment(current.get("updated_at"), expected_updated_at):
            raise StaleDraft(current.get("updated_at"), str(current.get("updated_by") or ""))
        query["updated_at"] = current.get("updated_at")        # and nobody saves in between
    sets: Dict[str, Any] = {**s.model_dump(), "updated_at": now, "updated_by": by}
    if reset_facts:
        sets["ai_unconfirmed"], sets["ai_fact_hashes"] = [], []
    else:
        previous = current.get("ai_fact_hashes")
        if previous is None:                                   # saved before facts were tracked by text
            old = fact_texts(settings_of(current))
            previous = [fact_hash(old[p]) for p in current.get("ai_unconfirmed") or [] if p in old]
        sets["ai_unconfirmed"], sets["ai_fact_hashes"] = track_unconfirmed(
            s, ai_unconfirmed or [], previous=previous, confirmed=ai_confirmed)
    res = await db[OFFERS].update_one(query, {"$set": sets})
    if not res.matched_count:
        if expected_updated_at is not None and await db[OFFERS].count_documents({"_id": offer_id}):
            current = await db[OFFERS].find_one({"_id": offer_id}, {"updated_at": 1, "updated_by": 1}) or {}
            raise StaleDraft(current.get("updated_at"), str(current.get("updated_by") or ""))
        return None
    return await db[OFFERS].find_one({"_id": offer_id})


def settings_of(doc: Mapping[str, Any]) -> OfferSettings:
    return OfferSettings.model_validate({k: v for k, v in doc.items() if k in OfferSettings.model_fields})


async def publish(db: Any, offer_id: str, *, by: str, by_name: str = "", now: Optional[datetime] = None) -> Dict[str, Any]:
    """Snapshot the draft as the next version. Refused (OfferError) while it has problems or AI-written
    facts nobody has confirmed."""
    now = now or _utcnow()
    doc = await db[OFFERS].find_one({"_id": offer_id})
    if doc is None:
        raise OfferError("Offer not found")
    if doc.get("status") == "archived":
        raise OfferError("Unarchive the offer before publishing it")
    s = settings_of(doc)
    problems, _ = validate_offer(s)
    problems += [i.message for i in unconfirmed_issues(doc.get("ai_unconfirmed") or [], s)]
    if problems:
        raise OfferError("Fix these before publishing", problems)
    body = s.model_dump()
    latest = int(doc.get("published_version") or 0)
    if latest:
        prev = await db[OFFER_VERSIONS].find_one({"_id": f"{offer_id}@{latest}"})
        if prev and prev.get("checksum") == checksum(body):
            return prev                              # nothing changed since the last version
    version = latest + 1
    snap = {"_id": f"{offer_id}@{version}", "offer_id": offer_id, "version": version, "settings": body,
            "checksum": checksum(body), "published_by": by, "published_by_name": by_name[:120],
            "published_at": now}
    await db[OFFER_VERSIONS].insert_one(snap)
    await db[OFFERS].update_one({"_id": offer_id}, {"$set": {"published_version": version, "status": "active",
                                                              "updated_at": now}})
    return snap


async def versions(db: Any, offer_id: str) -> List[Dict[str, Any]]:
    rows = await db[OFFER_VERSIONS].find({"offer_id": offer_id}, {"settings": 0}).sort("version", -1).to_list(200)
    return rows


async def get_version(db: Any, offer_id: str, version: int) -> Optional[Dict[str, Any]]:
    return await db[OFFER_VERSIONS].find_one({"_id": f"{offer_id}@{int(version)}"})


async def restore_version(db: Any, offer_id: str, version: int, *, by: str, now: Optional[datetime] = None,
                          expected_updated_at: Any = None) -> Dict[str, Any]:
    """Copy a published version's settings into the draft. The version itself never changes."""
    doc = await db[OFFERS].find_one({"_id": offer_id})
    if doc is None:
        raise OfferError("Offer not found")
    if doc.get("status") == "archived":
        raise OfferError("Unarchive the offer before restoring a version")
    snap = await get_version(db, offer_id, version)
    if snap is None:
        raise OfferError("Version not found")
    restored = await update_offer(db, offer_id, OfferSettings.model_validate(snap["settings"]), now=now, by=by,
                                  reset_facts=True, expected_updated_at=expected_updated_at)
    if restored is None:
        raise OfferError("Offer not found")
    return restored
