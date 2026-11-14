"""The hotel vertical — the pre-Phase-2 product, now a plug-in with its own room tools."""

from core.prompts.verticals import VerticalSpec, _labels
from core.tenancy.models import TenantConfig

HOTEL_TOOL_NAMES = (
    "check_room_availability", "get_room_pricing", "create_booking", "get_booking_details",
    "cancel_booking", "get_hotel_info", "send_booking_confirmation_whatsapp", "take_message",
    "answer_from_knowledge",
)


def _facts(t: TenantConfig) -> str:
    return f"""- Check-in from {t.hotel.checkin_time}, check-out by {t.hotel.checkout_time}. Prices are in {t.currency}.
- Room types: standard, deluxe, suite, executive, presidential. Use check_room_availability and
  get_room_pricing for anything about availability or price.
- Cancellation: free 3 or more days before arrival, 50% within 3 days, 100% on the day.
- Valid ID or passport is required at check-in. Children under 12 stay free with parents.
- For amenities, location and other policies, use get_hotel_info."""


SPEC = VerticalSpec(
    key="hotel",
    label="Hotel or guesthouse",
    description="Room availability, pricing, bookings and guest questions.",
    rules="""# Hotel rules
- Collect check-in date, check-out date, room type and number of guests, then check availability.
- Before booking or cancelling, read back the dates, room type and name, and get a clear yes.
- Only discuss a booking after the tools return it. Never read out someone else's details.""",
    tools=HOTEL_TOOL_NAMES,
    labels=_labels(appointment="Booking", contact="Guest", resource="Room", service="Room type"),
    keyterms=("deluxe", "suite", "presidential", "check-in", "check-out"),
    facts=_facts,
    uses_scheduling=False,
)
