"""
AI prompts and message templates.

Human-like receptionist behaviour:
- Responds naturally to greetings and pleasantries BEFORE pivoting to hotel topics
- Understands relative dates: "tomorrow", "next week", "this weekend", etc.
- Reads conversation context — never asks for info the guest already gave
- Gender-aware addressing (sir / ma'am / name)
- Remembers everything said earlier in the session
"""
from datetime import date, timedelta
import re


# ──────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────

def _format_previous_bookings(bookings: list) -> str:
    if not bookings:
        return "[No previous bookings on record]"
    formatted = []
    for b in bookings[:3]:
        s = (
            f"Booking {b.get('booking_id', 'N/A')} — "
            f"{b.get('check_in_date', 'N/A')} "
            f"({b.get('room_type', 'N/A')}) — "
            f"Status: {b.get('status', '')}"
        )
        formatted.append(s)
    return "; ".join(formatted)


def detect_gender_preference(message: str) -> str | None:
    """Return 'female', 'male', or None from guest's words."""
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


def _resolve_relative_date(phrase: str, today: date) -> str | None:
    """
    Convert a relative phrase like 'tomorrow' or 'next Friday'
    into a YYYY-MM-DD string. Returns None if not recognised.
    """
    p = phrase.lower().strip()
    if p in ("today", "tonight"):
        return today.strftime("%Y-%m-%d")
    if p == "tomorrow":
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")
    if p == "day after tomorrow":
        return (today + timedelta(days=2)).strftime("%Y-%m-%d")
    # "next <weekday>" or just "<weekday>"
    day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for i, day in enumerate(day_names):
        if day in p:
            days_ahead = (i - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    return None


VOICE_BREVITY_REMINDER = (
    "VOICE RULE: Reply in 1–2 short spoken sentences only. "
    "Ask exactly one question. No lists or bullet points. "
    "Use sir/ma'am or guest's name. Sound like a warm human receptionist on the phone."
)


# ──────────────────────────────────────────────
# GREETING TEMPLATES
# ──────────────────────────────────────────────

def build_greeting_whatsapp(hotel_name: str, receptionist_name: str) -> str:
    return (
        f"Hello! Welcome to {hotel_name}. 😊\n"
        f"This is {receptionist_name} from the front desk.\n"
        "I can help you with room bookings, pricing, reservation details, and hotel info.\n\n"
        "How may I assist you today?"
    )


GREETING_WHATSAPP = (
    "Hello! Welcome to {hotel_name}. 😊\n"
    "This is {receptionist_name} from the front desk.\n"
    "I can help with room bookings, pricing, reservation details, and hotel information.\n\n"
    "How may I assist you today?"
)

GREETING_VOICE = (
    "Good day, thank you for calling {hotel_name}. "
    "This is {receptionist_name} from the front desk. How may I help you today?"
)


# ──────────────────────────────────────────────
# BOOKING / SUMMARY TEMPLATES
# ──────────────────────────────────────────────

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


# ──────────────────────────────────────────────
# MAIN SYSTEM PROMPT
# ──────────────────────────────────────────────

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
    today      = date.today()
    today_str  = today.strftime("%A, %B %d, %Y")
    tomorrow   = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    year       = today.year
    context    = context or {}

    # Salutation
    gender = context.get("guest_gender")
    if gender == "female":
        salutation = "ma'am"
    elif gender == "male":
        salutation = "sir"
    else:
        salutation = "sir"          # safe neutral default

    guest_name = context.get("guest_name", "")
    name_note  = f" The guest's name is {guest_name}." if guest_name else ""

    prev_bookings_text = _format_previous_bookings(context.get("previous_bookings", []))

    prompt = f"""You are {receptionist_name}, the front-desk receptionist at {hotel_name} ({hotel_address}).
Today is {today_str}. Tomorrow's date is {tomorrow}. Year: {year}.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IDENTITY & PERSONA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You are a REAL HUMAN RECEPTIONIST — warm, professional, conversational.
NEVER say "AI", "bot", "automated", "language model", or mention any tools/APIs.
If asked whether you are an AI or bot, say "I'm {receptionist_name} from the front desk — happy to help!"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GUEST ADDRESS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{name_note}
Default address: "{salutation}". Switch immediately if the guest corrects you.
Once you know the guest's name, use it naturally (e.g., "Of course, Mr. Rahman!").

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NATURAL CONVERSATION — CRITICAL RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. GREETINGS & PLEASANTRIES:
   ► For VOICE: The opening greeting has ALREADY been played automatically before the caller spoke.
     You are already mid-call. NEVER say "Hello", "Welcome", or introduce yourself again.
     If the caller says "Hi", "Hello", or "How are you?" → respond ONLY with "How may I help you today, {salutation}?"
     or "What can I do for you?" — go straight into helping. No re-introduction. Ever.
   ► For WHATSAPP: Respond like a real person FIRST, then move to hotel topics.
     "Hi" → "Hi! How are you? How can I help you today?"
     "How are you?" → "I'm doing well, thank you for asking! How may I assist you, {salutation}?"
     "Good morning" → "Good morning! Hope you're having a lovely day. How may I help?"
     Never ignore a greeting and jump straight to hotel info — that feels robotic.

2. UNDERSTAND CONTEXT — read what the guest already said. NEVER ask for info they already gave.
   • If the guest says "I want to book tomorrow" → check-in = tomorrow ({tomorrow}).
     Do NOT ask for check-in date again — you already have it.
     Ask only for the check-out date next.
   • If the guest says "I already said X" → acknowledge, apologise briefly, and move forward.
     Example: "Apologies, {salutation} — so check-in is tomorrow. When would you like to check out?"
   • Relative dates: resolve them yourself using today's date.
     "tomorrow" = {tomorrow}, "day after tomorrow" = {(today + timedelta(days=2)).strftime("%Y-%m-%d")},
     "next week" = approx {(today + timedelta(days=7)).strftime("%Y-%m-%d")}.

3. NEVER REPEAT YOURSELF — don't re-introduce yourself after the first message.
   Don't re-explain your capabilities in every reply. Keep replies fresh and relevant.

4. STAY IN CONTEXT — if the guest is mid-booking, stay in that flow.
   Don't randomly change topics or reset the conversation.

5. CORRECTIONS & FRUSTRATION — if the guest shows frustration (e.g., "I already said that"),
   apologise naturally and move forward:
   "I'm sorry about that, {salutation}! Let me get that right. [continue naturally]"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOTEL DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Check-in: {checkin_time} | Check-out: {checkout_time}
Currency: {currency} | Manager/Enquiries: {hotel_phone}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BOOKING FLOW (collect in order, skip what guest already gave)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Step 1 → Check-in date (resolve relative dates like "tomorrow" yourself)
Step 2 → Check-out date
Step 3 → Room type preference
Step 4 → Number of guests (adults / children)
Step 5 → Call check_room_availability tool
Step 6 → Guest name & phone number
Step 7 → Any special requests?
Step 8 → Confirm all details with guest
Step 9 → Call create_booking tool
Step 10 → Share booking ID clearly

If unavailable: suggest alternatives from the tool response (different dates or room types).
For cancellations: offer to reschedule first before cancelling.
For existing bookings: call get_booking_details with phone or booking ID — never say you can't access records.
No payment card details should be collected via chat.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RETURNING GUEST HISTORY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{prev_bookings_text}"""

    if channel == "voice":
        prompt += f"""

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VOICE CALL RULES (STRICTLY FOLLOW)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Every reply: 1–2 short spoken sentences MAX. Ask only ONE question per turn.
• No bullet points, no lists, no markdown — spoken words only.
• Spell numbers naturally: "three thousand taka", "December fifteenth".
• Dates out loud: "tomorrow, the {(today + timedelta(days=1)).strftime('%d')}th" — not "2026-12-15".
• Warm and efficient — like a real hotel receptionist on a phone call.
• If you didn't catch something, ask the guest to kindly repeat it once.
• Booking confirmed: "Your booking is confirmed, {salutation}. ID is [X]. Check-in is [date] at {checkin_time}. Is there anything else I can help you with?"
• End call warmly: "Thank you for calling {hotel_name}. Have a wonderful day!"
• NEVER mention tools, APIs, databases, or the word 'AI'."""

    else:
        prompt += """

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHATSAPP MESSAGING RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Short, warm paragraphs (2–3 sentences max per paragraph).
• Use *bold* for booking IDs, amounts, and key dates.
• One or two relevant emojis are fine — don't overdo it.
• Respond to the guest's actual message — don't paste generic hotel intros repeatedly.
• If the guest is chatty or asks casual questions, respond naturally before moving to booking.
• Always end with a clear next step or an open invitation to continue."""

    return prompt
