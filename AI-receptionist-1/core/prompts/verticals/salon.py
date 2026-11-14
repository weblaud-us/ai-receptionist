from core.prompts.verticals import ALWAYS, SCHEDULING_TOOLS, VerticalSpec, _labels

SPEC = VerticalSpec(
    key="salon",
    label="Salon, barber, spa or beauty",
    description="Services with durations and prices, stylist choice, appointment booking and changes.",
    rules="""# Salon rules
- Ask which service, then preferred stylist (or "anyone"), then the day and time. Offer the
  nearest three open times, one sentence each.
- Quote prices only from the services list ("from" prices are starting prices).
- For a change or cancellation, find their appointment first, then offer to move it before cancelling.
- Running late, product questions or anything about a past visit → take a message.""",
    tools=SCHEDULING_TOOLS + ALWAYS,
    labels=_labels(appointment="Appointment", contact="Client", resource="Stylist", service="Service"),
    default_services=[
        {"name": "Haircut", "duration_min": 45, "buffer_min": 10, "price": {"amount": 4500, "currency": "USD", "display": "from $45"}},
        {"name": "Haircut & blow-dry", "duration_min": 60, "buffer_min": 10, "price": {"amount": 6500, "currency": "USD", "display": "from $65"}},
        {"name": "Colour", "duration_min": 120, "buffer_min": 15, "price": {"amount": 12000, "currency": "USD", "display": "from $120"}},
        {"name": "Beard trim", "duration_min": 20, "buffer_min": 5, "price": {"amount": 2000, "currency": "USD", "display": "$20"}},
    ],
    default_faqs=[
        {"question": "Do you take walk-ins?", "answer": "Yes when we have space, but booking is recommended."},
        {"question": "What is your cancellation policy?", "answer": "Please give us 24 hours' notice to cancel or move an appointment."},
        {"question": "Is there parking?", "answer": "Street parking is available nearby."},
    ],
    keyterms=("haircut", "blow-dry", "balayage", "highlights", "stylist"),
)
