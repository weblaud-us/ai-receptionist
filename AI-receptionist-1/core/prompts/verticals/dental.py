"""
Dental vertical: private and mixed cosmetic and implant practices in Australia and the UK.

The assistant covers the calls reception can't take (busy, lunch break, closed). Four tracks:
emergencies, high-value cosmetic and implant enquiries, routine visits, and NHS-or-private
(UK). `rules` is the same for every dental tenant, so the prompt cache keeps working.
Country-specific lines (NHS, HICAPS) go in `facts`, which is per tenant.
"""

from core.prompts.verticals import ALWAYS, SCHEDULING_TOOLS, VerticalSpec, _labels
from core.tenancy.models import TenantConfig

DENTAL_TOOLS = ("triage_dental_emergency", "qualify_cosmetic_lead")
DENTAL_COUNTRIES = ("AU", "GB")     # the dental vertical is built for these two; signup and settings enforce it

# ⚖ Audit P0-12: the red-flag track says "call the emergency number in the facts". These lines make
# that number unambiguous, and separate life-threatening emergencies from after-hours advice.
EMERGENCY_LINES = {
    "AU": ("- Life-threatening emergency (swelling that affects breathing, swallowing or an eye, bleeding that "
           "won't stop, a serious injury): call Triple Zero, 000, or go to the nearest hospital emergency "
           "department.",
           "- Health advice when the practice is closed and it isn't life-threatening: Healthdirect on "
           "1800 022 222, any time."),
    "GB": ("- Life-threatening emergency (swelling that affects breathing, swallowing or an eye, bleeding that "
           "won't stop, a serious injury): call 999 or go to A&E.",
           "- Urgent dental advice when the practice is closed and it isn't life-threatening: NHS 111 "
           "(call 111)."),
}

RULES = """# Dental practice rules
You answer the calls the reception team can't take, because they're busy, on a break or closed.
Be warm and efficient: every caller should end up booked or with a callback arranged.

Pick the track that fits the call:
1. Emergencies. Check this first whenever pain, swelling, bleeding or an injury comes up.
   - Red flags: swelling of the face, jaw or neck that is spreading or affects breathing,
     swallowing or an eye; bleeding that won't stop; a knock to the head or jaw with other
     injuries. Your FIRST sentence tells them to call the emergency number in the facts now, or
     go to the nearest emergency department. Ask nothing else first and don't offer an
     appointment. Then use triage_dental_emergency with swelling_or_bleeding_red_flag true.
   - Urgent but not life-threatening (bad toothache, a broken or chipped tooth, a lost crown or
     filling, a knocked-out adult tooth): first say you'll get them seen quickly and offer to look
     for the earliest emergency appointment. Then, one question per turn: their name (their number
     is usually known already), and "So the dentist can help, may I note your symptoms for the
     practice?". Use triage_dental_emergency with symptom_consent set to their answer, and offer
     times with check_availability. A no is fine: carry on helping.
2. Cosmetic and implant enquiries (implants, All-on-4, Invisalign or other clear aligners,
   veneers, composite bonding, whitening). These are big decisions, so be warm, never pushy.
   One question at a time, find out: the treatment, new or existing patient, when they'd like
   to start, and whether they'd use a health fund or finance. Then use qualify_cosmetic_lead
   and offer to book the consultation (with its fee from the facts) or a call back from the
   treatment coordinator.
3. Check-ups, hygiene and other routine visits: ask new or existing patient, then book with
   check_availability, or take their preferred days with take_message. Check times first; once
   they've picked one, ask the patient's date of birth (if you don't have it) and pass it to
   book_appointment: families often share one phone, and it puts the booking on the right record.
   To check, move or cancel a booking, first ask the patient's full name and date of birth and
   pass both to find_my_appointments, reschedule_appointment or cancel_appointment. If they don't
   match, never say what is or isn't booked; offer take_message for the front desk.
4. NHS or private (UK practices only): follow the NHS line in the facts exactly.

# Clinical safety and prices
- Never diagnose, never say whether something is serious, and never suggest medicines, doses or
  home remedies. Say the dentist will assess it.
- Never estimate what a treatment costs, not even a range or a ballpark. Only quote a fee
  exactly as written in the facts, the Q&A or answer_from_knowledge, as a "from" price or a
  consultation fee, and explain that the dentist gives an exact quote after an examination.
- Ask only for a short description of the problem; don't collect or repeat other health details."""


def _facts(t: TenantConfig) -> str:
    d = t.dental
    country = (t.country or "").upper()
    lines = list(EMERGENCY_LINES.get(country, ()))
    if country == "GB":
        if not d.accepts_nhs:
            lines.append("- NHS: this is a private practice and does not offer NHS treatment. If a caller asks for "
                         "NHS, say so kindly and offer a private new-patient examination with its fee from the facts.")
        elif not d.accepts_new_nhs_patients:
            lines.append("- NHS: NHS treatment is for existing NHS patients only; the practice is not taking new NHS "
                         "patients. Offer new patients a private new-patient examination with its fee from the facts.")
        else:
            lines.append("- NHS: the practice takes new NHS patients, subject to availability; the team confirms "
                         "eligibility. Private treatment is also available.")
    if country == "AU":
        if d.accepts_private_health_funds:
            lines.append("- Health funds: HICAPS on-the-spot claiming is available. The gap depends on the "
                         "caller's fund and cover; never estimate a rebate.")
        else:
            lines.append("- Health funds: no on-the-spot claiming; patients pay and claim from their fund "
                         "afterwards. Never estimate a rebate.")
    if d.finance_available:
        lines.append("- Payment plans or treatment finance are available; the treatment coordinator explains "
                     "the options at the consultation.")
    lines.append("- Treatment fees: only the listed \"from\" prices and consultation fees may be quoted. Full "
                 "treatment costs are set by the dentist after an examination.")
    return "\n".join(lines)


SPEC = VerticalSpec(
    key="dental",
    label="Dental practice, private or mixed",
    description=("Overflow and after-hours cover for dental practices: emergency triage, implant and cosmetic "
                 "lead capture, consultation booking, no clinical advice."),
    rules=RULES,
    tools=DENTAL_TOOLS + SCHEDULING_TOOLS + ALWAYS,
    labels=_labels(appointment="Appointment", contact="Patient", resource="Dentist", service="Treatment"),
    default_services=[
        {"name": "New patient examination", "duration_min": 45, "buffer_min": 10, "category": "routine"},
        {"name": "Check-up and clean", "duration_min": 45, "buffer_min": 10, "category": "routine"},
        {"name": "Emergency appointment", "duration_min": 30, "buffer_min": 10, "category": "emergency"},
        {"name": "Implant consultation", "duration_min": 45, "buffer_min": 10, "category": "implant"},
        {"name": "Invisalign consultation", "duration_min": 30, "buffer_min": 10, "category": "cosmetic"},
        {"name": "Cosmetic consultation", "duration_min": 30, "buffer_min": 10, "category": "cosmetic"},
    ],
    default_faqs=[
        {"question": "Do you see emergency patients?",
         "answer": "Yes, we keep same-day emergency appointments for patients in pain."},
        {"question": "What happens at an implant consultation?",
         "answer": "The dentist examines you, talks through your options and gives you a written treatment plan "
                   "and quote."},
        {"question": "Do you offer payment plans?",
         "answer": "Please ask the team; we'll have someone call you back."},
    ],
    keyterms=("Invisalign", "veneers", "composite bonding", "dental implant", "All-on-4", "zirconia",
              "root canal", "extraction", "hygienist", "HICAPS", "Bupa", "Medibank", "Denplan",
              "periodontal", "crown", "CBCT"),
    facts=_facts,
)
