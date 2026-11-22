"""
Questions callers commonly ask, per vertical — the Knowledge page offers the ones a business hasn't
answered yet, so the owner fills gaps before a caller hits them rather than after.

A suggestion counts as covered when any saved answer's question or tags contain every word of one of
its `keys` (after the same stemming the knowledge lookup uses).
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from core.tools.knowledge import _tokens


def _s(question: str, hint: str, *keys: str, countries: Sequence[str] = ()) -> Dict[str, Any]:
    return {"question": question, "hint": hint, "keys": keys, "countries": tuple(countries)}


COMMON = [
    _s("Are you open on public holidays?", "Which holidays you close, and any reduced hours.", "holiday"),
    _s("Is there parking nearby?", "Where to park, cost, time limits, disabled bays.", "park"),
    _s("How do I get there by public transport?", "Nearest station or bus stop and how far to walk.",
       "transport", "bus", "train", "tube", "tram", "station"),
    _s("Is the building wheelchair accessible?", "Steps, lifts, accessible toilet.", "wheelchair", "accessible",
       "accessibility", "lift"),
    _s("What payment methods do you take?", "Cards, cash, contactless, invoice.", "payment method", "pay",
       "card", "cash"),
    _s("What is your cancellation policy?", "How much notice you need and any fee.", "cancel", "cancellation"),
]

BY_VERTICAL: Dict[str, List[Dict[str, Any]]] = {
    "dental": [
        _s("Are you taking new patients?", "Yes/no, and how soon a new patient can usually be seen.",
           "new patient", "taking patient"),
        _s("Do you accept my health fund?", "Funds you're preferred providers for, and whether you claim on the spot (HICAPS).",
           "health fund", "hicaps", "insurance", "bupa", "medibank", countries=("AU",)),
        _s("Do you do NHS treatment?", "Whether you take NHS patients, the waiting list, private alternatives.",
           "nhs", countries=("GB", "UK")),
        _s("Do you take Denplan or other dental plans?", "Plans you accept and whether you have your own membership plan.",
           "denplan", "dental plan", "membership", countries=("GB", "UK")),
        _s("Do you bulk bill under the Child Dental Benefits Schedule?", "Whether eligible children are bulk billed.",
           "cdbs", "child dental benefit", "bulk bill", countries=("AU",)),
        _s("How much is a check-up and clean?", "Price range, and what's included (x-rays, scale and polish).",
           "check up", "checkup", "examination", "exam", "clean"),
        _s("Do you offer payment plans?", "Finance options (e.g. interest-free plans), minimum spend.",
           "payment plan", "finance", "afterpay", "zip", "instalment"),
        _s("What should I do in a dental emergency after hours?", "Who to call out of hours, when to go to hospital.",
           "after hour", "out of hour", "weekend emergency"),
        _s("Do you see children?", "From what age, and any kids' pricing.", "child", "children", "kid"),
        _s("I'm nervous about the dentist — can you help?", "Sedation, happy gas, longer appointments, gentle approach.",
           "nervous", "anxious", "anxiety", "sedation", "scared"),
        _s("How much does teeth whitening cost?", "In-chair vs take-home, price range.", "whiten"),
        _s("Do you offer Invisalign or clear aligners?", "Free consult? Typical price range.", "invisalign", "aligner",
           "brace"),
        _s("What should I bring to my first appointment?", "ID, health fund card, medication list, arrive early.",
           "bring", "first appointment", "first visit"),
    ],
    "clinic": [
        _s("Are you taking new patients?", "Yes/no, and how to register.", "new patient", "register"),
        _s("Do you bulk bill?", "Who is bulk billed and the gap fee for others.", "bulk bill", "gap", countries=("AU",)),
        _s("Do I need a referral?", "Which appointments need one.", "referral"),
        _s("Do you offer telehealth or phone appointments?", "Which appointment types can be remote.",
           "telehealth", "video", "phone appointment"),
        _s("How do I get a repeat prescription?", "How to request it and how long it takes.", "prescription", "script"),
        _s("How do I get my test results?", "Whether you call, text, or need an appointment.", "result"),
    ],
    "salon": [
        _s("Do I need a patch test?", "Which services need one and how far in advance.", "patch test", "allergy"),
        _s("Do you sell gift vouchers?", "Amounts, how to buy, expiry.", "voucher", "gift"),
        _s("What happens if I'm running late?", "How late before you shorten or rebook.", "late"),
        _s("Do you do children's haircuts?", "From what age and price.", "child", "children", "kid"),
        _s("What products do you use?", "Brands you use and sell.", "product", "brand"),
    ],
    "restaurant": [
        _s("Can you cater for allergies and dietary requirements?", "Allergens, gluten-free, how to tell staff.",
           "allergy", "dietary", "gluten", "coeliac"),
        _s("Do you have vegetarian or vegan options?", "How many dishes, any set menu.", "vegetarian", "vegan"),
        _s("Can I bring my own wine?", "BYO and corkage fee.", "byo", "corkage", "wine"),
        _s("Do you take bookings for large groups?", "Max group size, set menus, deposits.", "large group", "group",
           "party"),
        _s("Do you do takeaway or delivery?", "How to order, which apps.", "takeaway", "delivery", "take away"),
        _s("Do you have a kids' menu?", "Highchairs, kids' menu.", "child", "children", "kid", "highchair"),
    ],
    "hotel": [
        _s("What time is check-in and check-out?", "Times, early check-in / late check-out options.", "check in",
           "check out", "checkin", "checkout"),
        _s("Are pets allowed?", "Which pets, any fee.", "pet", "dog"),
        _s("Is breakfast included?", "Times, price if not included.", "breakfast"),
        _s("Do you have an airport transfer?", "Price, how to book.", "airport", "transfer", "shuttle"),
        _s("Is there Wi-Fi?", "Free or paid.", "wifi", "wi fi", "internet"),
    ],
    "home_services": [
        _s("What areas do you cover?", "Suburbs/towns or radius.", "area", "cover", "suburb", "postcode"),
        _s("Do you charge a call-out fee?", "Amount and whether it comes off the job.", "call out", "callout"),
        _s("Do you do emergency or after-hours work?", "Availability and rates.", "after hour", "emergency",
           "weekend"),
        _s("Are you licensed and insured?", "Licence numbers, insurance.", "licensed", "licence", "license", "insured", "insurance"),
        _s("Are quotes free?", "Free quotes? On site or by photo?", "quote"),
        _s("Do you guarantee your work?", "Warranty length.", "guarantee", "warranty"),
    ],
    "professional": [
        _s("Is the first consultation free?", "Length and cost of a first meeting.", "free consult", "first consult"),
        _s("How much do you charge?", "Fixed fees vs hourly, typical ranges.", "fee", "charge", "rate", "cost"),
        _s("What should I bring to my first meeting?", "Documents and information to prepare.", "bring", "document"),
        _s("Can we meet by video call?", "Video or phone meetings.", "video", "zoom", "teams", "online"),
    ],
}


def _covered_tokens(items: List[Dict[str, Any]]) -> List[set]:
    return [_tokens(f"{i.get('question', '')} {' '.join(i.get('tags') or [])}")
            for i in items if i.get("active") is not False]


def suggestions_for(vertical: str, country: str, items: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Suggested questions this business hasn't answered yet, most specific (vertical) first."""
    country = (country or "").upper()
    saved = _covered_tokens(items)
    out: List[Dict[str, str]] = []
    for s in [*BY_VERTICAL.get(vertical, []), *COMMON]:
        if s["countries"] and country not in s["countries"]:
            continue
        keys = [_tokens(k) for k in s["keys"]]
        if any(k and k <= words for k in keys for words in saved):
            continue
        out.append({"question": s["question"], "hint": s["hint"]})
    return out
