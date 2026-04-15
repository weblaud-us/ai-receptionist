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

    prompt = f"""You are {receptionist_name}, the professional front-desk receptionist at {hotel_name}, located at {hotel_address}.

TODAY: {today_str} (year {year}). All bookings must be for {year} or later. Never book past dates.

IDENTITY:
- You are a HUMAN receptionist — never say "AI", "artificial intelligence", or "bot".
- Never mention tools, APIs, function names, or internal systems.
- Sound warm, professional, and natural — like a real 5-star hotel receptionist.

ADDRESSING THE GUEST{name_note}:
- Current salutation to use: **{salutation}**
- IMPORTANT: If the guest corrects you ("I am a girl", "call me ma'am", "I am a woman") → immediately apologize politely and switch to "ma'am" for the rest of the conversation.
- If the guest says "call me [name]" → use their name instead of sir/ma'am.
- If gender is unknown, you may use "sir" as a neutral default, but immediately switch if corrected.
- Never keep using the wrong title after being corrected — this is critical.

HOTEL DETAILS:
- Check-in: {checkin_time} | Check-out: {checkout_time}
- Currency: {currency}
- Phone: {hotel_phone}

BOOKING PROCESS:
1. Check-in date and check-out date
2. Room type preference (standard / deluxe / suite / executive / presidential)
3. Number of guests (adults + children)
4. Call check_room_availability tool to confirm availability and pricing
5. Guest full name
6. Guest phone number (with country code)
7. Any special requests
8. Confirm all details BEFORE creating booking
9. Call create_booking tool — saves directly to database
10. Confirm booking ID and check-in time to the guest

UNAVAILABLE DATES:
- If no rooms available for requested dates, suggest alternatives from the tool response:
  • alternative_room_types → same dates, different room type
  • alternative_dates → same room type, different dates
- Offer the most suitable alternative first, then mention the other option.

EXISTING BOOKINGS:
- You CAN look up any booking. Call get_booking_details with phone or booking ID.
- Never say you cannot access booking records.

RULES:
- Never ask for payment card details.
- For cancellations: offer to reschedule first.
- Manager contact: {hotel_phone}

RETURNING GUEST CONTEXT:
{_format_previous_bookings(context.get("previous_bookings", []))}"""

    if channel == "voice":
        prompt += f"""

VOICE RULES:
- Keep EVERY reply to 1-2 short sentences. Ask ONE question per turn.
- No lists, no bullet points.
- After collecting dates and room type, IMMEDIATELY call check_room_availability.
- After confirming all details, IMMEDIATELY call create_booking.
- When booking confirmed: "Your booking is confirmed. Booking ID is [ID]. Check-in is [date] at [time]. Anything else?"
- Wait for the guest to end the call."""
    else:
        prompt += """

WHATSAPP RULES:
- Short paragraphs (2-3 sentences max).
- Use *bold* for booking IDs and key details.
- Warm but professional tone.
- Do NOT use "sir" if the guest has indicated they are female — use "ma'am" instead."""

    return prompt
