"""
AI prompts and message templates.

Key fix: Gender-aware addressing.
- Default: "sir" (neutral assumption)
- Guest reveals female ("I am a girl/woman/lady/mam/madam") → use "ma'am"
- Guest reveals male → use "sir"
- Guest prefers name → use name
"""
from datetime import date


def _format_previous_bookings(bookings: list) -> str:
    """Format previous bookings for prompt context."""
    if not bookings:
        return "[No previous bookings found]"
    formatted = []
    for booking in bookings[:3]:
        s = f"Booking {booking.get('booking_id','N/A')} - {booking.get('check_in_date','N/A')} ({booking.get('room_type','N/A')}) - {booking.get('status','')}"
        formatted.append(s)
    return "; ".join(formatted) if formatted else "[No previous bookings found]"


def detect_gender_preference(message: str) -> str | None:
    """
    Return 'female', 'male', or None based on what the guest says about themselves.
    Called after each user message; result stored in conversation context.
    """
    if not message:
        return None
    m = message.lower()
    female_signals = [
        "i am a girl", "i'm a girl", "i am a woman", "i'm a woman",
        "i am female", "i'm female", "call me mam", "call me ma'am",
        "call me madam", "not a boy", "not boy", "not sir",
        "i am lady", "i'm a lady", "she/her",
    ]
    male_signals = [
        "i am a man", "i'm a man", "i am male", "i'm male",
        "call me sir", "he/him", "i am a boy", "i'm a boy",
    ]
    if any(s in m for s in female_signals):
        return "female"
    if any(s in m for s in male_signals):
        return "male"
    return None


VOICE_BREVITY_REMINDER = (
    "VOICE RULE: Reply in 1-2 short spoken sentences only. "
    "Ask exactly one question. No bullet points or lists. "
    "Address the guest by their preferred title (sir/ma'am) or by name. "
    "Sound like a real human receptionist."
)


def build_greeting_whatsapp(hotel_name: str, receptionist_name: str) -> str:
    """Dynamic greeting — does NOT assume gender."""
    return (
        f"Hello! Welcome to {hotel_name}.\n"
        f"I am {receptionist_name} from the front desk.\n"
        "I can help with room bookings, pricing, existing reservations, and hotel information.\n\n"
        "How may I assist you today?"
    )


# Keep template for backward compat (used in some handlers)
GREETING_WHATSAPP = (
    "Hello! Welcome to {hotel_name}.\n"
    "I am {receptionist_name} from the front desk.\n"
    "I can help with room bookings, pricing, existing reservations, and hotel information.\n\n"
    "How may I assist you today?"
)

BOOKING_CONFIRMATION_WHATSAPP = (
    "✅ Your booking is confirmed!\n\n"
    "📋 Booking ID: *{booking_id}*\n"
    "👤 Guest: {guest_name}\n"
    "🏨 Room: {room_type} (Room {room_number})\n"
    "📅 Check-in: {check_in_date} at {checkin_time}\n"
    "📅 Check-out: {check_out_date} at {checkout_time}\n"
    "👥 Guests: {adults} adult(s){children_str}\n"
    "💰 Total: {currency} {total_amount:,.0f}\n"
    "📍 Address: {hotel_address}\n\n"
    "Please bring a valid ID at check-in. We look forward to welcoming you! 🌟"
)

GREETING_VOICE = (
    "Good day, thank you for calling {hotel_name}. "
    "This is {receptionist_name} from the front desk. How may I assist you today?"
)

CONVERSATION_SUMMARY_WHATSAPP = """📝 *Booking Summary*

Thank you for contacting *{hotel_name}*! Here are your booking details:

{booking_details}

📍 *Address:* {hotel_address}
🕐 *Check-in:* {checkin_time}  |  🕐 *Check-out:* {checkout_time}

Please bring a valid ID at check-in. We look forward to welcoming you! 🌟"""

SINGLE_BOOKING_DETAIL = """━━━━━━━━━━━━━━━━━━━━
📋 *Booking ID:* {booking_id}
👤 *Guest:* {guest_name}
🏨 *Room:* {room_type} (Room {room_number})
📅 *Check-in:* {check_in_date}
📅 *Check-out:* {check_out_date}
👥 *Guests:* {adults} adult(s){children_str}
💰 *Total:* {currency} {total_amount:,.0f}"""


def get_system_prompt(
    channel: str,
    hotel_name: str,
    receptionist_name: str,
    hotel_address: str,
    checkin_time: str,
    checkout_time: str,
    currency: str,
    hotel_phone: str,
    context: dict = None,
) -> str:
    today     = date.today()
    today_str = today.strftime("%A, %B %d, %Y")
    year      = today.year
    context   = context or {}

    # Determine correct salutation based on detected gender
    gender = context.get("guest_gender")  # "male" | "female" | None
    if gender == "female":
        salutation      = "ma'am"
        salutation_cap  = "Ma'am"
    elif gender == "male":
        salutation      = "sir"
        salutation_cap  = "Sir"
    else:
        salutation      = "sir or ma'am as appropriate"
        salutation_cap  = "Sir or Ma'am"

    guest_name = context.get("guest_name", "")
    name_note  = f" (you know their name is {guest_name})" if guest_name else ""

    prompt = f"""You are {receptionist_name}, front-desk receptionist at {hotel_name}, {hotel_address}.
TODAY: {today_str} (year {year}). Never book past dates.

IDENTITY: Human receptionist — never say "AI", "bot", or mention tools/APIs.

GUEST{name_note}: Use *{salutation}*. Switch immediately if corrected.
Hotel: Check-in {checkin_time} | Check-out {checkout_time} | {currency} | {hotel_phone}

BOOKING STEPS:
1. Check-in & check-out dates → 2. Room type → 3. Guest count
4. call check_room_availability → 5. Guest name & phone → 6. Special requests
7. Confirm details → 8. call create_booking → 9. Confirm booking ID

If unavailable: suggest alternative_room_types or alternative_dates from tool response.
Existing bookings: call get_booking_details with phone or booking ID — never say you can't access records.
No payment card details. For cancellations, offer reschedule first. Manager: {hotel_phone}

RETURNING GUEST: {_format_previous_bookings(context.get("previous_bookings", []))}"""

    if channel == "voice":
        prompt += f"""

VOICE RULES (CRITICAL — follow exactly):
- Every reply: 1-2 SHORT spoken sentences. ONE question per turn. No lists or bullets.
- Sound like a real hotel receptionist on the phone. Warm and efficient.
- Collect: check-in date → check-out date → room type → guests → call check_room_availability.
- Once guest confirms: get name + phone → call create_booking immediately.
- Booking confirmed: "Your booking is confirmed. ID is [X]. Check-in [date] at {checkin_time}. Anything else I can help with?"
- Never mention tools, APIs, or the word 'AI'."""
    else:
        prompt += """

WHATSAPP RULES:
- Short paragraphs (2-3 sentences max).
- Use *bold* for booking IDs and key details.
- Warm but professional tone.
- Do NOT use "sir" if the guest has indicated they are female — use "ma'am" instead."""

    return prompt
