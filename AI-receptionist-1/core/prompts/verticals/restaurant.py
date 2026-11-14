from core.prompts.verticals import ALWAYS, SCHEDULING_TOOLS, VerticalSpec, _labels
from core.tenancy.models import TenantConfig


def _facts(t: TenantConfig) -> str:
    return f"- Reservations are for parties of up to {t.scheduling.max_party_size}. Larger groups: take a message for the manager."


SPEC = VerticalSpec(
    key="restaurant",
    label="Restaurant, café or bar",
    description="Reservations by party size, hours and menu questions, messages for the manager.",
    rules="""# Restaurant rules
- For a reservation collect party size, date, time and a name, then check_availability with the
  party size. Offer the nearest times if the requested one is full.
- Answer hours, location, menu and dietary questions from the facts. Don't invent dishes or prices.
- Takeaway or delivery orders: say you can't take orders by phone yet and give the website or
  take a message, whichever the facts say.
- Large parties, private events and complaints → take a message for the manager.""",
    tools=SCHEDULING_TOOLS + ALWAYS,
    labels=_labels(appointment="Reservation", contact="Guest", resource="Table", service="Seating"),
    default_services=[{"name": "Table reservation", "duration_min": 90, "buffer_min": 15}],
    default_faqs=[
        {"question": "Do you have vegetarian or vegan options?", "answer": "Yes, several on every menu."},
        {"question": "Is there parking?", "answer": "There is a car park behind the building."},
        {"question": "Can I bring a cake?", "answer": "Yes, let us know when you book."},
    ],
    keyterms=("reservation", "party of", "table", "patio", "vegan", "gluten"),
    facts=_facts,
)
