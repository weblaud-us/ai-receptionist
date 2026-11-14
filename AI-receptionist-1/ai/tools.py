"""
Hotel AI Agent Tools
These are called by the AI agent to perform real hotel operations.
Each tool directly queries/writes MongoDB.
"""

import json
import logging
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any

from pymongo.errors import BulkWriteError, DuplicateKeyError

from config import settings
from core.phone import digits, phones_match
from core.timeutils import business_today
from database.mongodb import get_db
from database.tenancy import get_hotel_profile
from database.models import Booking, BookingStatus, RoomType, PricingBreakdown
from services.calendar import build_booking_calendar_links, generate_calendar_token

logger = logging.getLogger(__name__)


def _hotel_today() -> date:
    return business_today(get_hotel_profile().get("timezone"))


def new_booking_id() -> str:
    return f"HTL{uuid.uuid4().hex[:8].upper()}"


def _nights(check_in_date: str, check_out_date: str) -> List[str]:
    """Each night a stay occupies, as local YYYY-MM-DD strings (check-out day excluded)."""
    start, end = date.fromisoformat(check_in_date), date.fromisoformat(check_out_date)
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days)]


def phone_variants(phone: str) -> List[str]:
    """Stored formats a phone might be saved in: E.164, digits only, and BD national (0…)."""
    d = digits(phone)
    variants = {phone.replace(" ", "").replace("-", ""), d, f"+{d}"}
    if d.startswith("880"):
        variants.add("0" + d[3:])            # +8801712345678 → 01712345678
    elif d.startswith("0"):
        variants.update({"880" + d[1:], "+880" + d[1:]})
    return sorted(v for v in variants if v)


async def release_room_nights(db: Any, booking_id: str, from_night: Optional[str] = None) -> int:
    """Free a booking's room-nights (all, or from `from_night` on). Returns how many were freed."""
    query: Dict[str, Any] = {"booking_id": booking_id}
    if from_night:
        query["night"] = {"$gte": from_night}
    result = await db.room_nights.delete_many(query)
    return result.deleted_count


# ─────────────────────────────────────────────
# Caller-ownership rules for booking tools (voice + WhatsApp)
# ─────────────────────────────────────────────

BOOKING_ACCESS_TOOLS = {"get_booking_details", "cancel_booking", "send_booking_confirmation_whatsapp"}

# Same answer whether the booking doesn't exist or belongs to someone else: never leak existence.
NOT_FOUND: Dict[str, Any] = {
    "error": "not_found",
    "message": (
        "No matching booking found. If the guest is calling from a different number than the "
        "one on the booking, ask for the booking ID and the full name on the booking."
    ),
}


def _norm_name(name: Optional[str]) -> List[str]:
    return sorted(re.sub(r"[^\w\s]", " ", (name or "").casefold()).split())


def _names_match(a: Optional[str], b: Optional[str]) -> bool:
    ta, tb = _norm_name(a), _norm_name(b)
    return bool(ta) and ta == tb


def _caller_owns(booking: Dict[str, Any], caller_phone: Optional[str]) -> bool:
    return bool(caller_phone) and phones_match(booking.get("guest_phone"), caller_phone)


def _identity_proven(booking: Dict[str, Any], guest_name: Optional[str], check_in_date: Optional[str]) -> bool:
    """Booking ID (already used to find it) + full name or check-in date = two factors."""
    if _names_match(booking.get("guest_name"), guest_name):
        return True
    return bool(check_in_date) and str(booking.get("check_in_date")) == str(check_in_date).strip()


# ─────────────────────────────────────────────
# TOOL DEFINITIONS (OpenAI function format)
# ─────────────────────────────────────────────

HOTEL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_room_availability",
            "description": "Check which hotel rooms are available for given dates. Always call this before quoting prices or confirming bookings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "check_in_date": {
                        "type": "string",
                        "description": "Check-in date in YYYY-MM-DD format"
                    },
                    "check_out_date": {
                        "type": "string",
                        "description": "Check-out date in YYYY-MM-DD format"
                    },
                    "room_type": {
                        "type": "string",
                        "enum": ["standard", "deluxe", "suite", "executive", "presidential"],
                        "description": "Preferred room type (optional)"
                    },
                    "adults": {
                        "type": "integer",
                        "description": "Number of adult guests"
                    },
                    "children": {
                        "type": "integer",
                        "description": "Number of children"
                    }
                },
                "required": ["check_in_date", "check_out_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_room_pricing",
            "description": "Get detailed pricing for a specific room type including taxes, discounts for specific dates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "room_type": {
                        "type": "string",
                        "enum": ["standard", "deluxe", "suite", "executive", "presidential"]
                    },
                    "check_in_date": {
                        "type": "string",
                        "description": "Check-in date YYYY-MM-DD"
                    },
                    "check_out_date": {
                        "type": "string",
                        "description": "Check-out date YYYY-MM-DD"
                    }
                },
                "required": ["room_type", "check_in_date", "check_out_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_booking",
            "description": "Create a confirmed hotel room booking after collecting all guest details.",
            "parameters": {
                "type": "object",
                "properties": {
                    "guest_name": {"type": "string", "description": "Full name of the guest"},
                    "guest_phone": {"type": "string", "description": "Guest phone number with country code"},
                    "guest_email": {"type": "string", "description": "Guest email (optional)"},
                    "room_type": {
                        "type": "string",
                        "enum": ["standard", "deluxe", "suite", "executive", "presidential"]
                    },
                    "check_in_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "check_out_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "adults": {"type": "integer"},
                    "children": {"type": "integer"},
                    "special_requests": {"type": "string", "description": "Any special requests from guest"}
                },
                "required": ["guest_name", "guest_phone", "room_type", "check_in_date", "check_out_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_booking_details",
            "description": (
                "Retrieve the caller's existing bookings. Without a booking ID it looks up bookings "
                "made with the caller's own phone number. If the guest is contacting you from a "
                "different number, ask for the booking ID and the full name on the booking."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_id": {"type": "string", "description": "Booking ID like HTL3A4B5C6D"},
                    "guest_phone": {"type": "string", "description": "Guest phone number to find their bookings"},
                    "guest_name": {"type": "string", "description": "Full name on the booking (needed when calling from another number)"},
                    "check_in_date": {"type": "string", "description": "Check-in date YYYY-MM-DD (alternative proof of identity)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_booking",
            "description": (
                "Cancel an existing booking by booking ID. If the guest is contacting you from a "
                "different number than the one on the booking, also pass the full name on the booking."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_id": {"type": "string"},
                    "cancellation_reason": {"type": "string"},
                    "guest_name": {"type": "string", "description": "Full name on the booking"},
                    "check_in_date": {"type": "string", "description": "Check-in date YYYY-MM-DD"}
                },
                "required": ["booking_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_hotel_info",
            "description": "Get general hotel information: amenities, policies, check-in/out times, location, facilities.",
            "parameters": {
                "type": "object",
                "properties": {
                    "info_type": {
                        "type": "string",
                        "enum": ["general", "amenities", "policies", "location", "rooms_overview"],
                        "description": "Type of information requested"
                    }
                },
                "required": ["info_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_booking_confirmation_whatsapp",
            "description": "Send booking confirmation details to guest's WhatsApp number. Use this after creating a booking to send confirmation via WhatsApp.",
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_id": {"type": "string", "description": "The booking ID to send confirmation for"},
                    "whatsapp_number": {"type": "string", "description": "Guest's WhatsApp number with country code (e.g., +8801712345678)"},
                    "guest_name": {"type": "string", "description": "Full name on the booking (needed when calling from another number)"}
                },
                "required": ["booking_id", "whatsapp_number"]
            }
        }
    },
]


# ─────────────────────────────────────────────
# TOOL EXECUTOR
# ─────────────────────────────────────────────

class HotelToolExecutor:
    """
    Executes AI agent tool calls against MongoDB.

    `execute()` is the ONLY entry point for the AI (voice + WhatsApp). It enforces the
    caller-ownership rules for booking tools. The plain methods below are also called by the
    authenticated dashboard API, which is allowed to see every booking.
    """

    def __init__(self):
        self.db = None

    def _get_db(self):
        return get_db()

    async def execute(self, tool_name: str, arguments: Dict, *, caller_phone: Optional[str] = None) -> str:
        """Route an AI tool call to its handler and return a JSON string result."""
        try:
            arguments = dict(arguments or {})
            if tool_name in BOOKING_ACCESS_TOOLS:
                result = await self._execute_booking_access(tool_name, arguments, caller_phone)
                return json.dumps(result, default=str)

            handlers = {
                "check_room_availability": self.check_room_availability,
                "get_room_pricing": self.get_room_pricing,
                "create_booking": self.create_booking,
                "get_hotel_info": self.get_hotel_info,
            }
            handler = handlers.get(tool_name)
            if not handler:
                return json.dumps({"error": f"Unknown tool: {tool_name}"})

            result = await handler(**arguments)
            return json.dumps(result, default=str)

        except (ValueError, TypeError) as e:
            # Bad arguments from the model (e.g. a malformed date): tell it so it can retry.
            logger.warning("Tool %s got invalid arguments: %s", tool_name, type(e).__name__)
            return json.dumps({"error": "invalid_arguments", "message": str(e)[:200]})
        except Exception as e:
            logger.error("Tool %s failed: %s", tool_name, type(e).__name__, exc_info=True)
            return json.dumps({"error": "tool_failed", "message": "Something went wrong. Apologise and offer to try again."})

    # ── Booking access (ownership-checked) ────────────────────────────────

    async def _execute_booking_access(
        self, tool_name: str, args: Dict[str, Any], caller_phone: Optional[str]
    ) -> Dict[str, Any]:
        """
        Rule (Task 0.9): a booking may be read or changed only if
          - its guest_phone matches the caller's phone, or
          - the caller gives the booking ID plus the full name (or check-in date) on it.
        Anything else returns NOT_FOUND — identical to a booking that doesn't exist.
        """
        db = self._get_db()
        booking_id = (args.get("booking_id") or "").strip().upper()
        guest_name = args.get("guest_name")
        check_in_date = args.get("check_in_date")

        if tool_name == "get_booking_details" and not booking_id:
            # Lookup by phone: only ever the caller's own phone.
            requested = args.get("guest_phone")
            if not caller_phone or (requested and not phones_match(requested, caller_phone)):
                return NOT_FOUND
            result = await self.get_booking_details(guest_phone=caller_phone)
            return result if result.get("found") else NOT_FOUND

        if not booking_id:
            return {"error": "booking_id_required", "message": "Ask the guest for their booking ID."}

        booking = await db.bookings.find_one({"booking_id": booking_id})
        if not booking:
            return NOT_FOUND
        owns = _caller_owns(booking, caller_phone)
        if not owns and not _identity_proven(booking, guest_name, check_in_date):
            return NOT_FOUND

        if tool_name == "get_booking_details":
            return await self.get_booking_details(booking_id=booking_id)

        if tool_name == "cancel_booking":
            result = await self.cancel_booking(booking_id, args.get("cancellation_reason"))
            if result.get("success") and not owns:
                # Cancelled by someone calling from another number: tell the phone on file.
                await self._notify_cancellation_to_phone_on_file(booking)
            return result

        # send_booking_confirmation_whatsapp
        destination = args.get("whatsapp_number") if owns else None
        return await self.send_booking_confirmation_whatsapp(
            booking_id=booking_id,
            whatsapp_number=destination or booking.get("guest_phone", ""),
        )

    async def _notify_cancellation_to_phone_on_file(self, booking: Dict[str, Any]) -> None:
        phone = booking.get("guest_phone")
        if not phone or not settings.ENABLE_WHATSAPP:
            return
        try:
            from whatsapp.meta_handler import _send_meta_whatsapp

            profile = get_hotel_profile()
            await _send_meta_whatsapp(
                phone,
                f"Your booking {booking.get('booking_id')} at {profile['name']} "
                f"(check-in {booking.get('check_in_date')}) has been cancelled. "
                f"If you did not request this, please contact us at {profile['phone']}.",
                None,
            )
        except Exception:
            logger.warning("Cancellation notice to phone on file failed", exc_info=True)

    async def check_room_availability(
        self,
        check_in_date: str,
        check_out_date: str,
        room_type: Optional[str] = None,
        adults: int = 1,
        children: int = 0,
    ) -> Dict:
        db = self._get_db()
        checkin = date.fromisoformat(check_in_date)
        checkout = date.fromisoformat(check_out_date)

        if checkin >= checkout:
            return {"error": "Check-out must be after check-in"}
        if checkin < _hotel_today():
            return {"error": "Check-in date cannot be in the past"}

        nights = (checkout - checkin).days

        # Find all active rooms
        room_filter: Dict[str, Any] = {"is_active": True, "is_maintenance": False}
        if room_type:
            room_filter["room_type"] = room_type
        total_capacity = adults + children
        room_filter["capacity"] = {"$gte": total_capacity}

        rooms = await db.rooms.find(room_filter).to_list(length=100)

        # Find conflicting bookings
        # IMPORTANT: For same-day check-out/check-in, checkout is at 12:00, check-in is at 2:00 PM
        # So a booking that checks out on checkin_date should be considered available for same-day check-in
        booked_room_ids = set()
        conflicting = await db.bookings.find({
            "status": {"$in": ["confirmed", "pending", "checked_in"]},
            "$or": [
                # Booking overlaps during check-in period
                {"check_in_date": {"$lt": check_out_date, "$gt": check_in_date}},  # Starts during stay
                # Booking overlaps at checkout or after
                {"check_out_date": {"$gt": check_in_date, "$lt": check_out_date}},  # Ends during stay
                # Booking completely contains stay period
                {"check_in_date": {"$lte": check_in_date}, "check_out_date": {"$gte": check_out_date}},
            ]
        }).to_list(length=500)

        for b in conflicting:
            booked_room_ids.add(b["room_id"])

        available = []
        for room in rooms:
            room_id = str(room.get("_id", ""))
            if room_id not in booked_room_ids:
                price = room["pricing"]["base_price_per_night"]
                total = self._price_stay(room["pricing"], nights)["final_total"]
                available.append({
                    "room_id": room_id,
                    "room_number": room["room_number"],
                    "room_type": room["room_type"],
                    "bed_type": room["bed_type"],
                    "floor": room["floor"],
                    "capacity": room["capacity"],
                    "price_per_night": price,
                    "total_price": round(total, 2),
                    "currency": get_hotel_profile()["currency"],
                    "amenities": room.get("amenities", {}),
                    "description": room.get("description", ""),
                })

        # If requested room_type not available, find alternatives
        alternative_room_types = None
        if len(available) == 0 and room_type:
            # Requested room type not available, try other types
            alternative_room_types = await self._find_alternative_room_types(
                db, checkin, checkout, room_type, total_capacity, nights, booked_room_ids
            )

        # If still not available, find next available dates for same room type
        alternative_dates = None
        if len(available) == 0:
            alternative_dates = await self._find_alternative_dates(
                db, checkin, checkout, room_type, total_capacity, nights
            )

        return {
            "available": len(available) > 0,
            "rooms": available,
            "check_in_date": check_in_date,
            "check_out_date": check_out_date,
            "nights": nights,
            "total_available": len(available),
            "alternative_room_types": alternative_room_types or [],  # Different room types for same dates
            "alternative_dates": alternative_dates or [],  # Same room type for different dates
        }

    async def _find_alternative_room_types(
        self,
        db: Any,
        checkin: date,
        checkout: date,
        requested_room_type: str,
        total_capacity: int,
        nights: int,
        booked_room_ids: set,
    ) -> List[Dict]:
        """Find available rooms of different types when requested type is booked."""
        room_types = ["standard", "deluxe", "suite", "executive", "presidential"]
        alternatives = []

        # Priority: suggest better/cheaper alternatives
        type_priority = {
            "standard": ["deluxe", "suite", "executive", "presidential"],
            "deluxe": ["suite", "executive", "presidential", "standard"],
            "suite": ["executive", "presidential", "deluxe", "standard"],
            "executive": ["presidential", "suite", "deluxe", "standard"],
            "presidential": ["executive", "suite", "deluxe", "standard"],
        }

        suggested_types = type_priority.get(requested_room_type, room_types)

        for alt_type in suggested_types:
            if alt_type == requested_room_type:
                continue

            # Find rooms of this type
            room_filter = {"is_active": True, "is_maintenance": False, "room_type": alt_type, "capacity": {"$gte": total_capacity}}
            alt_rooms = await db.rooms.find(room_filter).to_list(length=100)

            for room in alt_rooms:
                room_id = str(room.get("_id", ""))
                if room_id not in booked_room_ids:
                    price = room["pricing"]["base_price_per_night"]
                    total = price * nights * (1 + room["pricing"].get("tax_pct", 15) / 100)
                    alternatives.append({
                        "room_type": alt_type,
                        "room_number": room["room_number"],
                        "bed_type": room["bed_type"],
                        "price_per_night": price,
                        "total_price": round(total, 2),
                        "upgrade": alt_type != requested_room_type,  # Is this an upgrade?
                    })
                    # Return first available of this type
                    break

            if len(alternatives) >= 3:  # Max 3 alternatives
                break

        return alternatives

    async def _find_alternative_dates(
        self,
        db: Any,
        original_checkin: date,
        original_checkout: date,
        room_type: Optional[str],
        total_capacity: int,
        nights: int,
    ) -> List[Dict]:
        """Find alternative available dates when requested dates aren't available."""
        alternatives = []
        
        # Check next 14 days for available dates
        for offset in range(1, 15):
            new_checkin = original_checkin + timedelta(days=offset)
            new_checkout = new_checkin + timedelta(days=nights)
            
            # Check if this new date range is available
            room_filter: Dict[str, Any] = {"is_active": True, "is_maintenance": False}
            if room_type:
                room_filter["room_type"] = room_type
            room_filter["capacity"] = {"$gte": total_capacity}
            
            available_count = 0
            
            # Count available rooms for this date range
            all_rooms = await db.rooms.find(room_filter).to_list(length=100)
            
            booked_ids = set()
            conflicts = await db.bookings.find({
                "status": {"$in": ["confirmed", "pending", "checked_in"]},
                "$or": [
                    {"check_in_date": {"$lt": new_checkout.isoformat(), "$gte": new_checkin.isoformat()}},
                    {"check_out_date": {"$gt": new_checkin.isoformat(), "$lte": new_checkout.isoformat()}},
                    {"check_in_date": {"$lte": new_checkin.isoformat()}, "check_out_date": {"$gte": new_checkout.isoformat()}},
                ]
            }).to_list(length=500)
            
            for b in conflicts:
                booked_ids.add(b["room_id"])
            
            available_count = sum(1 for r in all_rooms if str(r.get("_id")) not in booked_ids)
            
            if available_count > 0:
                alternatives.append({
                    "check_in_date": new_checkin.isoformat(),
                    "check_out_date": new_checkout.isoformat(),
                    "available_rooms": available_count,
                })
                # Return first 3 alternatives
                if len(alternatives) >= 3:
                    break
        
        return alternatives

    @staticmethod
    def _price_stay(pricing: Dict, nights: int) -> Dict[str, float]:
        """Single source of truth for a stay's price (weekly/monthly discount, then tax)."""
        base = pricing["base_price_per_night"]
        base_total = base * nights
        discount = 0.0
        if nights >= 30:
            discount = base_total * (pricing.get("discount_monthly_pct", 0) / 100)
        elif nights >= 7:
            discount = base_total * (pricing.get("discount_weekly_pct", 0) / 100)
        taxable = base_total - discount
        tax = taxable * (pricing.get("tax_pct", 15) / 100)
        return {
            "base_total": round(base_total, 2),
            "discount": round(discount, 2),
            "tax": round(tax, 2),
            "final_total": round(taxable + tax, 2),
        }

    async def get_room_pricing(
        self,
        room_type: str,
        check_in_date: str,
        check_out_date: str,
        room_id: Optional[str] = None,
    ) -> Dict:
        db = self._get_db()
        checkin = date.fromisoformat(check_in_date)
        checkout = date.fromisoformat(check_out_date)
        nights = (checkout - checkin).days
        if nights < 1:
            return {"error": "Check-out must be after check-in"}

        room = None
        if room_id:
            room = await db.rooms.find_one({"_id": room_id, "is_active": True})
        if not room:
            room = await db.rooms.find_one({"room_type": room_type, "is_active": True})
        if not room:
            return {"error": f"No {room_type} rooms found"}

        quote = self._price_stay(room["pricing"], nights)
        base = room["pricing"]["base_price_per_night"]

        return {
            "room_type": room_type,
            "price_per_night": base,
            "nights": nights,
            "base_total": quote["base_total"],
            "discount": quote["discount"],
            "tax_15pct": quote["tax"],          # legacy key name (tax % is per room)
            "tax": quote["tax"],
            "final_total": quote["final_total"],
            "currency": get_hotel_profile()["currency"],
            "amenities_included": ["WiFi", "AC", "TV"] + (["Breakfast"] if room["amenities"].get("breakfast_included") else []),
        }

    async def create_booking(
        self,
        guest_name: str,
        guest_phone: str,
        room_type: str,
        check_in_date: str,
        check_out_date: str,
        guest_email: Optional[str] = None,
        adults: int = 1,
        children: int = 0,
        special_requests: Optional[str] = None,
        room_id: Optional[str] = None,
        source: str = "ai_receptionist",
    ) -> Dict:
        db = self._get_db()

        # Double-check availability
        avail = await self.check_room_availability(
            check_in_date, check_out_date, room_type, adults, children
        )
        if avail.get("error"):
            return {"error": avail["error"]}
        if not avail.get("available"):
            return {"error": f"No {room_type} rooms available for those dates. {avail.get('total_available', 0)} rooms available."}

        rooms = avail["rooms"]
        if not rooms:
            return {"error": "No rooms match your requirements"}

        if room_id:
            # Caller asked for a specific room — it must still be free.
            candidates = [r for r in rooms if r["room_id"] == room_id]
            if not candidates:
                return {"error": "The selected room is no longer available for those dates"}
        else:
            candidates = rooms

        # Claim the room-nights. The unique (room_id, night) index means the DATABASE refuses a
        # second booking for the same room and night — two concurrent callers can't both win.
        # If another booking grabbed a room between the availability check and now, try the next.
        booking_id = new_booking_id()
        nights_list = _nights(check_in_date, check_out_date)
        chosen = None
        for candidate in candidates:
            if await self._claim_room_nights(db, candidate["room_id"], booking_id, nights_list):
                chosen = candidate
                break
        if chosen is None:
            return {
                "error": "no_longer_available",
                "message": "That room was just booked by someone else. Offer the guest other rooms or dates.",
            }

        try:
            return await self._finalise_booking(
                db, booking_id, chosen, guest_phone=guest_phone, guest_name=guest_name,
                guest_email=guest_email, room_type=room_type, check_in_date=check_in_date,
                check_out_date=check_out_date, adults=adults, children=children,
                special_requests=special_requests, source=source, nights=avail["nights"],
            )
        except Exception:
            await release_room_nights(db, booking_id)  # don't leave orphan claims
            raise

    async def _claim_room_nights(self, db: Any, room_id: str, booking_id: str, nights: List[str]) -> bool:
        now = datetime.utcnow()
        docs = [{"room_id": room_id, "night": n, "booking_id": booking_id, "created_at": now} for n in nights]
        try:
            await db.room_nights.insert_many(docs, ordered=True)
            return True
        except (DuplicateKeyError, BulkWriteError):
            await release_room_nights(db, booking_id)  # undo any nights inserted before the clash
            return False

    async def _finalise_booking(
        self, db: Any, booking_id: str, chosen: Dict[str, Any], *, guest_phone: str,
        guest_name: str, guest_email: Optional[str], room_type: str, check_in_date: str,
        check_out_date: str, adults: int, children: int, special_requests: Optional[str],
        source: str, nights: int,
    ) -> Dict[str, Any]:
        # Calculate pricing from the room actually being booked
        pricing_data = await self.get_room_pricing(
            room_type, check_in_date, check_out_date, room_id=chosen["room_id"]
        )
        if pricing_data.get("error"):
            await release_room_nights(db, booking_id)
            return {"error": pricing_data["error"]}

        pricing = PricingBreakdown(
            base_total=pricing_data["base_total"],
            taxes=pricing_data["tax_15pct"],
            discounts=pricing_data["discount"],
            final_total=pricing_data["final_total"],
            per_night_avg=pricing_data["price_per_night"],
            nights=nights,
            currency=get_hotel_profile()["currency"],
        )

        # Upsert guest profile
        await db.guests.update_one(
            {"phone": guest_phone},
            {
                "$set": {
                    "name": guest_name,
                    "email": guest_email,
                    "last_interaction": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                },
                "$inc": {"total_stays": 0},  # don't increment yet
                "$setOnInsert": {"created_at": datetime.utcnow(), "vip_tier": "standard"},
            },
            upsert=True,
        )

        # Create booking (its room-nights are already claimed under this booking_id)
        booking = Booking(
            booking_id=booking_id,
            guest_phone=guest_phone,
            guest_name=guest_name,
            guest_email=guest_email,
            room_id=chosen["room_id"],
            room_number=chosen["room_number"],
            room_type=RoomType(room_type),
            check_in_date=date.fromisoformat(check_in_date),
            check_out_date=date.fromisoformat(check_out_date),
            adults=adults,
            children=children,
            special_requests=special_requests,
            pricing=pricing,
            status=BookingStatus.CONFIRMED,
            source=source,
        )

        doc = booking.model_dump(by_alias=True)
        # Convert dates to strings for MongoDB
        doc["check_in_date"] = check_in_date
        doc["check_out_date"] = check_out_date
        doc["calendar_token"] = generate_calendar_token(booking.booking_id, guest_phone)
        doc["one_day_reminder_sent"] = False
        doc["one_day_reminder_sent_at"] = None
        await db.bookings.insert_one(doc)

        # Update guest stay count
        await db.guests.update_one(
            {"phone": guest_phone},
            {"$inc": {"total_stays": 1}}
        )

        profile = get_hotel_profile()
        calendar_links = build_booking_calendar_links(
            booking_id=booking.booking_id,
            calendar_token=doc["calendar_token"],
            hotel_id=profile["hotel_id"],
            hotel_name=profile["name"],
            hotel_address=profile["address"],
            check_in_date=check_in_date,
            check_out_date=check_out_date,
            checkin_time=profile["checkin_time"],
            checkout_time=profile["checkout_time"],
            room_type=room_type,
        )

        logger.info("✅ Booking created: %s (room %s)", booking.booking_id, chosen["room_number"])

        return {
            "success": True,
            "booking_id": booking.booking_id,
            "guest_name": guest_name,
            "room_number": chosen["room_number"],
            "room_type": room_type,
            "check_in_date": check_in_date,
            "check_out_date": check_out_date,
            "adults": adults,
            "children": children,
            "nights": nights,
            "total_amount": pricing_data["final_total"],
            "currency": profile["currency"],
            "status": "confirmed",
            "check_in_time": profile["checkin_time"],
            "check_out_time": profile["checkout_time"],
            "hotel_address": profile["address"],
            "hotel_name": profile["name"],
            "hotel_id": profile["hotel_id"],
            "calendar_ics_url": calendar_links["ics_url"],
            "calendar_google_url": calendar_links["google_calendar_url"],
        }

    async def get_booking_details(
        self,
        booking_id: Optional[str] = None,
        guest_phone: Optional[str] = None,
    ) -> Dict:
        db = self._get_db()
        if not booking_id and not guest_phone:
            return {"error": "Provide booking_id or guest_phone"}

        query = {}
        if booking_id:
            query["booking_id"] = booking_id.upper()
        elif guest_phone:
            query["guest_phone"] = {"$in": phone_variants(guest_phone)}

        bookings = await db.bookings.find(query).sort("created_at", -1).limit(5).to_list(5)

        if not bookings:
            return {"found": False, "message": "No bookings found"}

        result = []
        for b in bookings:
            result.append({
                "booking_id": b["booking_id"],
                "guest_name": b["guest_name"],
                "room_number": b["room_number"],
                "room_type": b["room_type"],
                "check_in_date": str(b["check_in_date"]),
                "check_out_date": str(b["check_out_date"]),
                "status": b["status"],
                "total_amount": b["pricing"]["final_total"],
                "currency": b["pricing"]["currency"],
            })

        return {"found": True, "bookings": result, "count": len(result)}

    async def cancel_booking(
        self,
        booking_id: str,
        cancellation_reason: Optional[str] = None,
    ) -> Dict:
        db = self._get_db()
        booking = await db.bookings.find_one({"booking_id": booking_id.upper()})

        if not booking:
            return {"error": f"Booking {booking_id} not found"}

        if booking["status"] in ["checked_out", "cancelled"]:
            return {"error": f"Cannot cancel a booking with status: {booking['status']}"}

        # Check-in is today or passed - no free cancellation
        checkin = date.fromisoformat(str(booking["check_in_date"]))
        today = _hotel_today()
        days_until_checkin = (checkin - today).days

        cancellation_fee = 0
        if days_until_checkin < 1:
            cancellation_fee = booking["pricing"]["final_total"]
            note = "100% cancellation fee applies (same day)"
        elif days_until_checkin < 3:
            cancellation_fee = booking["pricing"]["final_total"] * 0.5
            note = "50% cancellation fee applies (less than 3 days)"
        else:
            note = "Free cancellation"

        await db.bookings.update_one(
            {"booking_id": booking_id.upper()},
            {
                "$set": {
                    "status": "cancelled",
                    "updated_at": datetime.utcnow(),
                },
                "$push": {
                    "booking_notes": f"Cancelled: {cancellation_reason or 'No reason given'}"
                }
            }
        )
        await release_room_nights(db, booking_id.upper())  # the room is bookable again

        return {
            "success": True,
            "booking_id": booking_id.upper(),
            "status": "cancelled",
            "cancellation_fee": cancellation_fee,
            "currency": get_hotel_profile()["currency"],
            "note": note,
        }

    async def get_hotel_info(self, info_type: str) -> Dict:
        info_map = {
            "general": {
                "name": get_hotel_profile()["name"],
                "address": get_hotel_profile()["address"],
                "phone": get_hotel_profile()["phone"],
                "check_in_time": get_hotel_profile()["checkin_time"],
                "check_out_time": get_hotel_profile()["checkout_time"],
                "stars": 5,
                "description": f"{get_hotel_profile()['name']} offers world-class hospitality.",
            },
            "amenities": {
                "facilities": ["Swimming Pool", "Spa & Wellness Center", "Fitness Center", "Business Center",
                               "Multiple Restaurants", "Rooftop Bar", "Valet Parking", "24/7 Room Service",
                               "Concierge Service", "Airport Transfer", "Laundry Service"],
                "dining": ["The Grand Restaurant (International)", "Spice Garden (Bengali & Asian)", "Sky Lounge Bar"],
            },
            "policies": {
                "check_in": get_hotel_profile()["checkin_time"],
                "check_out": get_hotel_profile()["checkout_time"],
                "early_check_in": "Available from 10:00 AM (subject to availability, extra charge may apply)",
                "late_check_out": "Until 2:00 PM (subject to availability)",
                "cancellation": "Free cancellation 3+ days before arrival. 50% fee within 3 days. 100% same day.",
                "pets": "Not allowed",
                "smoking": "Non-smoking property. Designated areas available.",
                "children": "Children under 12 stay free with parents",
                "id_required": "Valid national ID or passport required at check-in",
            },
            "location": {
                "address": get_hotel_profile()["address"],
                "landmarks": ["5 min from Bashundhara City", "10 min from Hazrat Shahjalal Airport", "Near Gulshan-2"],
                "transport": "Airport transfer available (please book in advance)",
            },
            "rooms_overview": {
                "room_types": {
                    "standard": "Comfortable rooms with city view, queen bed, all essentials",
                    "deluxe": "Spacious rooms with premium furnishings and upgraded amenities",
                    "suite": "Separate living area, king bed, panoramic views",
                    "executive": "Executive floor access, lounge privileges, premium services",
                    "presidential": "Ultimate luxury with private butler, multiple rooms, VIP amenities",
                }
            }
        }
        return info_map.get(info_type, {"error": "Unknown info type"})

    async def send_booking_confirmation_whatsapp(
        self,
        booking_id: str,
        whatsapp_number: str,
    ) -> Dict:
        """Send booking confirmation to guest's WhatsApp via Meta API"""
        import httpx
        
        db = self._get_db()
        
        logger.info("📱 Sending WhatsApp confirmation for booking %s", booking_id)
        
        # Find the booking
        booking = await db.bookings.find_one({"booking_id": booking_id.upper()})
        if not booking:
            logger.warning("Booking %s not found", booking_id)
            return {"success": False, "error": f"Booking {booking_id} not found"}
        
        
        # Get Meta WhatsApp credentials
        token = (settings.META_WA_ACCESS_TOKEN or "").strip()
        phone_number_id = (settings.META_WA_PHONE_NUMBER_ID or "").strip()
        
        if not token:
            logger.error("META_WA_ACCESS_TOKEN missing")
            return {"success": False, "error": "WhatsApp not configured - META_WA_ACCESS_TOKEN missing"}
        
        if not phone_number_id:
            logger.error("META_WA_PHONE_NUMBER_ID missing")
            return {"success": False, "error": "WhatsApp not configured - META_WA_PHONE_NUMBER_ID missing"}
        
        # Get hotel profile for message details
        profile = get_hotel_profile()
        
        # Build confirmation message
        children_str = f" + {booking.get('children', 0)} children" if booking.get("children", 0) > 0 else ""
        pricing = booking.get("pricing", {})
        
        message = (
            f"Sir, booking confirmed! ✅\n\n"
            f"📋 Booking ID: {booking.get('booking_id')}\n"
            f"👤 Guest: {booking.get('guest_name')}\n"
            f"🏨 Room: {str(booking.get('room_type', '')).title()} (Room {booking.get('room_number', '-')})\n"
            f"📅 Check-in: {booking.get('check_in_date')} at {profile['checkin_time']}\n"
            f"📅 Check-out: {booking.get('check_out_date')} at {profile['checkout_time']}\n"
            f"👥 Guests: {booking.get('adults', 1)} adult(s){children_str}\n"
            f"💰 Total: {pricing.get('currency', 'BDT')} {pricing.get('final_total', 0):,.0f}\n\n"
            f"📍 Hotel Address: {profile['address']}\n\n"
            f"Please bring a valid ID at check-in.\n"
            f"We look forward to welcoming you, sir! 🌟"
        )
        
        # Format phone number (remove + for Meta API)
        to_phone = whatsapp_number.replace("+", "").replace(" ", "").replace("-", "")
        
        # Send via Meta WhatsApp API
        api_version = (settings.META_GRAPH_API_VERSION or "v20.0").strip()
        url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "text",
            "text": {"body": message},
        }
        
        try:
            
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(url, headers=headers, json=payload)
                
                if resp.status_code >= 400:
                    logger.error("Meta WhatsApp send failed: HTTP %s", resp.status_code)
                    return {"success": False, "error": f"WhatsApp send failed (HTTP {resp.status_code})"}
                
                logger.info("✅ WhatsApp confirmation sent for %s", booking_id)
                
                # Update booking to mark confirmation sent
                await db.bookings.update_one(
                    {"booking_id": booking_id.upper()},
                    {"$set": {"whatsapp_confirmation_sent": True, "whatsapp_confirmation_sent_at": datetime.utcnow()}}
                )
                
                return {
                    "success": True,
                    "message": f"Booking confirmation sent to WhatsApp {whatsapp_number}",
                    "booking_id": booking_id
                }
                
        except Exception as e:
            logger.error("WhatsApp send error: %s", type(e).__name__, exc_info=True)
            return {"success": False, "error": "WhatsApp send failed"}


# Singleton executor
_executor: Optional[HotelToolExecutor] = None

def get_tool_executor() -> HotelToolExecutor:
    global _executor
    if not _executor:
        _executor = HotelToolExecutor()
    return _executor
