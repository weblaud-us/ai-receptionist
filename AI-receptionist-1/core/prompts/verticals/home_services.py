from core.prompts.verticals import ALWAYS, SCHEDULING_TOOLS, VerticalSpec, _labels

SPEC = VerticalSpec(
    key="home_services",
    label="Home services (plumbing, HVAC, electrical, roofing, cleaning)",
    description="Capture every job with address and urgency, transfer emergencies, book estimate visits.",
    rules="""# Home services rules
- For any job, collect: name, callback number, the service address, a short description of the
  problem, and how urgent it is. Then use capture_lead. Read the number and address back first.
- Emergencies — flooding, a gas smell, sparks or burning smell, no heat in freezing weather, no
  water — are urgent: say you'll get someone right away and try transfer_to_human. If that fails,
  capture the lead as an emergency and say the team will call back immediately.
- Never quote a price for a job; say the team will confirm pricing. You may read fixed call-out
  fees from the business facts.
- If they want a visit booked and estimate visits are offered, use check_availability.""",
    tools=("capture_lead",) + SCHEDULING_TOOLS + ALWAYS,
    labels=_labels(appointment="Job", contact="Customer", resource="Technician", service="Service"),
    default_services=[
        {"name": "Estimate visit", "duration_min": 60, "buffer_min": 30},
        {"name": "Service call", "duration_min": 90, "buffer_min": 30},
    ],
    default_faqs=[
        {"question": "Do you offer emergency service?", "answer": "Yes, 24/7 for emergencies. Emergency call-outs carry a surcharge."},
        {"question": "Are you licensed and insured?", "answer": "Yes, fully licensed and insured."},
        {"question": "Do you give free estimates?", "answer": "Estimates for new work are free; diagnostic visits have a call-out fee."},
    ],
    keyterms=("water heater", "furnace", "AC", "leak", "breaker", "estimate"),
)
