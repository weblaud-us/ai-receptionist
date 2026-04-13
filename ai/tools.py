"""
Hotel AI Agent Tools
These are called by the AI agent to perform real hotel operations.
Each tool directly queries/writes MongoDB.
"""

import json
import logging
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any
from bson import ObjectId

from config import settings
from database.mongodb import get_db
from database.tenancy import get_hotel_profile
from database.models import (
    Booking, BookingStatus, Room, RoomType,
    GuestProfile, PricingBreakdown
)
from services.calendar import build_booking_calendar_links, generate_calendar_token

logger = logging.getLogger(__name__)


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
            "description": "Retrieve existing booking details by booking ID or guest phone number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_id": {"type": "string", "description": "Booking ID like HTL3A4B5C6D"},
                    "guest_phone": {"type": "string", "description": "Guest phone number to find their bookings"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_booking",
            "description": "Cancel an existing booking by booking ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_id": {"type": "string"},
                    "cancellation_reason": {"type": "string"}
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
                    "whatsapp_number": {"type": "string", "description": "Guest's WhatsApp number with country code (e.g., +8801712345678)"}
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
    """Executes AI agent tool calls against MongoDB"""

    def __init__(self):
        self.db = None

    def _get_db(self):
        return get_db()

    async def execute(self, tool_name: str, arguments: Dict) -> str:
        """Route tool call to correct handler, return JSON string result"""
        try:
            handlers = {
                "check_room_availability": self.check_room_availability,
                "get_room_pricing": self.get_room_pricing,
                "create_booking": self.create_booking,
                "get_booking_details": self.get_booking_details,
                "cancel_booking": self.cancel_booking,
                "get_hotel_info": self.get_hotel_info,
                "send_booking_confirmation_whatsapp": self.send_booking_confirmation_whatsapp,
            }
            handler = handlers.get(tool_name)
            if not handler:
                return json.dumps({"error": f"Unknown tool: {tool_name}"})

            result = await handler(**arguments)
            return json.dumps(result, default=str)

        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}", exc_info=True)
            return json.dumps({"error": str(e)})

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
        if checkin < date.today():
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
                total = price * nights * (1 + room["pricing"].get("tax_pct", 15) / 100)
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

    async def get_room_pricing(
        self,
        room_type: str,
        check_in_date: str,
        check_out_date: str,
    ) -> Dict:
        db = self._get_db()
        checkin = date.fromisoformat(check_in_date)
        checkout = date.fromisoformat(check_out_date)
        nights = (checkout - checkin).days

        room = await db.rooms.find_one({"room_type": room_type, "is_active": True})
        if not room:
            return {"error": f"No {room_type} rooms found"}

        pricing = room["pricing"]
        base = pricing["base_price_per_night"]
        base_total = base * nights

        # Apply discounts
        discount = 0.0
        if nights >= 30:
            discount = base_total * (pricing.get("discount_monthly_pct", 0) / 100)
        elif nights >= 7:
            discount = base_total * (pricing.get("discount_weekly_pct", 0) / 100)

        taxable = base_total - discount
        tax = taxable * (pricing.get("tax_pct", 15) / 100)
        final = taxable + tax

        return {
            "room_type": room_type,
            "price_per_night": base,
            "nights": nights,
            "base_total": round(base_total, 2),
            "discount": round(discount, 2),
            "tax_15pct": round(tax, 2),
            "final_total": round(final, 2),
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
    ) -> Dict:
        db = self._get_db()

        # Double-check availability
        avail = await self.check_room_availability(
            check_in_date, check_out_date, room_type, adults, children
        )
        if not avail.get("available"):
            return {"error": f"No {room_type} rooms available for those dates. {avail.get('total_available', 0)} rooms available."}

        rooms = avail["rooms"]
        if not rooms:
            return {"error": "No rooms match your requirements"}

        # Pick first available room
        chosen = rooms[0]

        # Calculate pricing
        nights = avail["nights"]
        pricing_data = await self.get_room_pricing(room_type, check_in_date, check_out_date)

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

        # Create booking
        booking = Booking(
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
            source="ai_receptionist",
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

        logger.info(f"✅ Booking created: {booking.booking_id} for {guest_name}")

        # ── Real-time dashboard broadcast ──────────────────────────────────────
        try:
            import asyncio
            from routers.admin_extras import broadcast_new_booking
            asyncio.create_task(broadcast_new_booking(
                booking_id=booking.booking_id,
                guest_name=guest_name,
                status="confirmed",
                amount=pricing_data["final_total"],
            ))
        except Exception:
            pass  # non-critical

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
            # Normalize phone: try multiple formats (E.164, local BD, without +)
            raw = guest_phone.replace(" ", "").replace("-", "")
            phone_variants = {raw}
            if raw.startswith("+"):
                no_plus = raw[1:]
                phone_variants.add(no_plus)
                if raw.startswith("+880"):
                    phone_variants.add("0" + raw[4:])   # +8801712345678 → 01712345678
            elif raw.startswith("880"):
                phone_variants.add("+" + raw)           # 8801712345678 → +8801712345678
                phone_variants.add("0" + raw[3:])       # 8801712345678 → 01712345678
            elif raw.startswith("0"):
                phone_variants.add("880" + raw[1:])     # 01712345678 → 8801712345678
                phone_variants.add("+880" + raw[1:])    # 01712345678 → +8801712345678
            query["guest_phone"] = {"$in": list(phone_variants)}

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

    async def _get_guest_bookings_by_phone(
        self,
        phone: str,
        limit: int = 5,
    ) -> List[Dict]:
        """
        Retrieve guest's recent bookings by phone (used by Twilio voice to inject into greeting).
        Returns minimal info for prompt context (not for display).
        """
        db = self._get_db()
        
        # Normalize phone: same logic as get_booking_details
        raw = phone.replace(" ", "").replace("-", "")
        phone_variants = {raw}
        if raw.startswith("+"):
            no_plus = raw[1:]
            phone_variants.add(no_plus)
            if raw.startswith("+880"):
                phone_variants.add("0" + raw[4:])  # +8801712345678 → 01712345678
        elif raw.startswith("880"):
            phone_variants.add("+" + raw)          # 8801712345678 → +8801712345678
            phone_variants.add("0" + raw[3:])      # 8801712345678 → 01712345678
        elif raw.startswith("0"):
            phone_variants.add("880" + raw[1:])    # 01712345678 → 8801712345678
            phone_variants.add("+880" + raw[1:])   # 01712345678 → +8801712345678
        
        # Query recent bookings
        bookings = await db.bookings.find(
            {"guest_phone": {"$in": list(phone_variants)}}
        ).sort("created_at", -1).limit(limit).to_list(limit)
        
        # Return simplified format for voice greeting context
        result = []
        for b in bookings:
            result.append({
                "booking_id": b["booking_id"],
                "guest_name": b.get("guest_name", "Guest"),
                "check_in_date": str(b["check_in_date"]),
                "check_out_date": str(b["check_out_date"]),
                "room_type": b["room_type"],
                "room_number": b.get("room_number", "TBD"),
                "status": b["status"],  # confirmed, checked_in, checked_out, cancelled, etc
            })
        
        return result

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
        today = date.today()
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
        booking["status"] = "cancelled"
        booking["updated_at"] = datetime.utcnow()

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
        from database.tenancy import get_hotel_profile
        
        db = self._get_db()
        
        logger.info(f"📱 Attempting to send WhatsApp confirmation for booking {booking_id} to {whatsapp_number}")
        
        # Find the booking
        booking = await db.bookings.find_one({"booking_id": booking_id.upper()})
        if not booking:
            logger.error(f"Booking {booking_id} not found in database")
            return {"success": False, "error": f"Booking {booking_id} not found"}
        
        logger.info(f"Found booking: {booking.get('booking_id')} for {booking.get('guest_name')}")
        
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
            logger.info(f"Sending WhatsApp to {to_phone} via Meta API")
            
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(url, headers=headers, json=payload)
                
                if resp.status_code >= 400:
                    logger.error(f"Meta WhatsApp send failed: {resp.status_code} {resp.text}")
                    return {"success": False, "error": f"WhatsApp send failed: {resp.text}"}
                
                logger.info(f"✅ WhatsApp sent successfully: {resp.status_code}")
                
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
            logger.error(f"WhatsApp send error: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


# Backward compatibility: older modules may still import ToolExecutor.
class ToolExecutor(HotelToolExecutor):
    pass


# Singleton executor
_executor: Optional[HotelToolExecutor] = None

def get_tool_executor() -> HotelToolExecutor:
    global _executor
    if not _executor:
        _executor = HotelToolExecutor()
    return _executor
