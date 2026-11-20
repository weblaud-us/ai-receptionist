"""
The sales persona (Roadmap Task 4.5): a different job from reception. The agent leads, qualifies,
handles gatekeepers and objections, and gives up gracefully.

Two things are fixed in code, not left to the model:
- The opener is spoken word for word (`opener()`), so every call starts by saying it is an AI, who
  is calling and on whose behalf, and how to opt out. `check_opener` is the test for that.
- Personas are versions of the same script (`PERSONAS`). A campaign can name two; leads are split
  between them by a stable hash so the demo rate of each can be compared (Task 4.6 A/B testing).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, List, Mapping, Sequence

from config import settings

VERTICAL_LABELS: Mapping[str, str] = {
    "home_services": "home-service", "salon": "salon and spa", "restaurant": "restaurant", "clinic": "clinic",
    "dental": "dental practice", "professional": "professional-services", "hotel": "hotel",
}

_OPT_OUT_LINE = "If you'd rather not get calls like this, just say so and I'll take you off our list."


@dataclass(frozen=True)
class Persona:
    id: str
    label: str
    cold_opener: str          # {persona}, {company}, {business}
    pitch: str
    first_question: str


PERSONAS: Mapping[str, Persona] = {
    "sales_v1": Persona(
        id="sales_v1", label="Direct: ask for the owner",
        cold_opener=("Hi, this is {persona}, an AI assistant calling from {company}. Am I speaking with the "
                     "owner or manager of {business}? " + _OPT_OUT_LINE),
        pitch=("Our AI answers every call 24/7, books jobs into your calendar and texts you the details, "
               "for less than a part-time receptionist. Would a quick 15-minute demo this week be useful?"),
        first_question="Who answers the phone when you're busy or after hours?",
    ),
    "sales_v2": Persona(
        id="sales_v2", label="Missed calls first",
        cold_opener=("Hi, this is {persona}, an AI assistant calling from {company}, about missed calls at "
                     "{business}. Is the owner or manager around? " + _OPT_OUT_LINE),
        pitch=("We answer the calls you can't get to, day or night, book the job and text you the details. "
               "Could I set up a 15-minute demo so you can hear it?"),
        first_question="What happens to a call when nobody can pick up?",
    ),
}
DEFAULT_PERSONA = "sales_v1"

_CONSENTED_OPENER = ("Hi, this is {persona}, an AI assistant from {company}. You asked us for a call about our "
                     "AI receptionist{for_business}. Is now a good time? If you'd rather not hear from us, "
                     "just say so and I'll take you off our list.")
_RECORDING_NOTICE = " This call may be recorded."

VOICEMAIL_MESSAGE = ("Hi, this is {persona}, an AI assistant from {company}, returning your request for a call "
                     "about our AI receptionist. You can reach us on {callback}. If you'd prefer no more calls, "
                     "call that number and tell us. Thanks, goodbye.")


def get(persona_id: str) -> Persona:
    return PERSONAS.get(persona_id) or PERSONAS[DEFAULT_PERSONA]


def pick(persona_ids: Sequence[str], lead_id: str) -> str:
    """Stable 50/50 split: the same lead always gets the same version."""
    ids: List[str] = [p for p in persona_ids if p in PERSONAS] or [DEFAULT_PERSONA]
    digest = hashlib.sha256(lead_id.encode("utf-8")).digest()
    return ids[digest[0] % len(ids)]


def _business(lead: Mapping[str, Any]) -> str:
    return str(lead.get("business_name") or "").strip() or "your business"


def opener(persona: Persona, lead: Mapping[str, Any], mode: str, *, recording: bool = False) -> str:
    """The exact first sentence(s) of the call."""
    names = {"persona": settings.SALES_PERSONA_NAME, "company": settings.SALES_COMPANY_NAME}
    if mode == "ai_consented":
        business = str(lead.get("business_name") or "").strip()
        text = _CONSENTED_OPENER.format(for_business=f" for {business}" if business else "", **names)
    else:
        text = persona.cold_opener.format(business=_business(lead), **names)
    return text + (_RECORDING_NOTICE if recording else "")


def check_opener(text: str) -> List[str]:
    """What a legal opener must contain. Empty list = fine."""
    low = text.lower()
    problems = []
    if " ai " not in f" {low} " and "an ai" not in low:
        problems.append("does not say it is an AI")
    if settings.SALES_COMPANY_NAME.lower() not in low:
        problems.append("does not name the company")
    if "off our list" not in low and "opt out" not in low:
        problems.append("does not offer a way to opt out")
    return problems


def voicemail_message() -> str:
    return VOICEMAIL_MESSAGE.format(persona=settings.SALES_PERSONA_NAME, company=settings.SALES_COMPANY_NAME,
                                    callback=settings.SALES_DEMO_LINE or "the number on your caller ID")


def build_prompt(persona: Persona, lead: Mapping[str, Any], campaign: Mapping[str, Any], *,
                 can_transfer: bool = False) -> str:
    company = settings.SALES_COMPANY_NAME
    founder = settings.SALES_FOUNDER_NAME or "our founder"
    vertical = VERTICAL_LABELS.get(str(lead.get("vertical") or campaign.get("vertical") or ""), "local")
    contact = str(lead.get("contact_name") or "").strip()
    consented = campaign.get("mode") == "ai_consented"
    demo_line = settings.SALES_DEMO_LINE
    lines = [
        "# Who you are",
        f"You are {settings.SALES_PERSONA_NAME}, an AI assistant calling on behalf of {company}, which provides "
        f"AI phone receptionists for {vertical} businesses. You are an AI. You have already said so in your "
        "opening line; say so again whenever you are asked.",
        "",
        "# This call",
        f"You are calling {_business(lead)}" + (f"; the contact on file is {contact}" if contact else "") + ".",
        ("They filled in a form asking for this call." if consented else
         "They are not expecting your call. Be brief and respectful of their time."),
        "You have already given your opener. Do not repeat it. Listen to their answer and continue.",
        "",
        "# Goal",
        f"Find out if missed calls cost them business. If so, book a 15-minute demo with {founder}.",
        "Success = demo booked. Second best = permission to email information. Otherwise end politely.",
        "",
        "# Rules",
        "- This is a phone call: plain spoken sentences, no lists, no symbols. Under 25 words per turn. Ask, then listen.",
        "- If they say stop, not interested, take me off, or don't call: apologise once, call add_to_dnc, say "
        "goodbye, call end_call. Never argue. Never ask why.",
        "- If you reach a gatekeeper: ask for the best time to reach the owner (then schedule_callback), or "
        "permission to email. Never pretend to be a customer or an existing contact.",
        "- Never claim results you can't back up. Never pressure. Never misrepresent who you are or why you are calling.",
        "- Never quote a price, a discount or a contract term. Prices are covered in the demo.",
        f"- If asked something you don't know, offer to have {founder} follow up by email.",
        "- If this is clearly not the business you meant to call, apologise, call mark_wrong_number, then end_call.",
        "- Offer times only from check_demo_slots. Never invent a time. Confirm the day, time and their email "
        "before book_demo.",
        "- Email only. Do not offer to text them.",
        "",
        "# Qualification (ask naturally, one at a time, not as a survey)",
        f"1. {persona.first_question}",
        "2. Roughly how many calls a day?",
        "3. What happens to calls nobody answers?",
        "4. Do they book appointments or jobs by phone?",
        "",
        "# Pitch (one or two sentences, tailored to their answers)",
        f'"{persona.pitch}"',
        "",
        "# Objections",
        '- "Too expensive": ask what one missed job is worth to them; mention there is a free trial.',
        '- "Customers want a human": it transfers to them whenever a caller asks'
        + (f"; they can hear it now on our demo line, {demo_line}." if demo_line else "."),
        '- "Send me an email": confirm the email address, call send_info_email, then offer a demo time anyway.',
        '- "Is this a robot?": "Yes, I\'m an AI, the same kind of AI that would answer your phone." Lean into it.',
        '- "Call me later": ask when, then call schedule_callback.',
    ]
    if can_transfer:
        lines.append("- If they want to talk to a person right now, say you'll connect them and call transfer_to_founder.")
    lines += [
        "",
        "# Ending",
        "When the conversation is over, say a short goodbye and call end_call.",
    ]
    return "\n".join(lines)
