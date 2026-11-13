"""
REST API Routers (all protected by `require_admin`, except the tokenised .ics link)
- /bookings  — hotel bookings: search, create, edit, status changes, cancel
- /rooms     — room inventory: list, create, edit, deactivate, availability
"""

import logging
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from ai.tools import get_tool_executor
from database.models import (
    BedType, BookingCreateRequest, BookingStatus, Room, RoomAmenities,
    RoomPricing, RoomType,
)
from database.mongodb import get_db
from database.tenancy import get_hotel_profile
from services.calendar import generate_booking_ics
from utils.auth import require_admin
from utils.serialize import to_jsonable

logger = logging.getLogger(__name__)

bookings_router = APIRouter(
    prefix="/bookings", tags=["Bookings"], dependencies=[Depends(require_admin)]
)
rooms_router = APIRouter(
    prefix="/rooms", tags=["Rooms"], dependencies=[Depends(require_admin)]
)
# Public: guests open this link from WhatsApp; it is protected by its own token.
calendar_router = APIRouter(prefix="/bookings", tags=["Bookings"])

PAYMENT_STATUSES = ["pending", "partial", "paid"]
ACTIVE_BOOKING_STATUSES = ["confirmed", "pending", "checked_in"]


def _clean(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Expose Mongo `_id` as `id`."""
    if doc is None:
        return doc
    _id = doc.pop("_id", None)
    if _id is not None and "id" not in doc:
        doc["id"] = str(_id)
    return to_jsonable(doc)


# ─────────────────────────────────────────────
# BOOKINGS API
# ─────────────────────────────────────────────

@bookings_router.get("/lookup/search")
async def lookup_booking(
    phone: Optional[str] = Query(None),
    booking_id: Optional[str] = Query(None),
    guest_name: Optional[str] = Query(None),
):
    """Lookup bookings by phone number, booking ID, or guest name."""
    db = get_db()
    query: Dict[str, Any] = {}

    if booking_id:
        query["booking_id"] = booking_id.upper()
    elif phone:
        digits = re.sub(r"[^\d]", "", phone)
        if not digits:
            return {"bookings": [], "total": 0}
        query["guest_phone"] = {"$in": [f"+{digits}", digits]}
    elif guest_name:
        query["guest_name"] = {"$regex": re.escape(guest_name), "$options": "i"}
    else:
        return {"bookings": [], "message": "Please provide phone, booking_id, or guest_name"}

    docs = await db.bookings.find(query).sort("created_at", -1).limit(10).to_list(10)
    bookings = []
    for doc in docs:
        bookings.append({
            "booking_id": doc.get("booking_id"),
            "guest_name": doc.get("guest_name"),
            "guest_phone": doc.get("guest_phone"),
            "room_type": doc.get("room_type"),
            "check_in_date": doc.get("check_in_date"),
            "check_out_date": doc.get("check_out_date"),
            "status": doc.get("status"),
            "num_guests": doc.get("adults", 1) + doc.get("children", 0),
            "special_requests": doc.get("special_requests", ""),
            "pricing": doc.get("pricing", {}),
        })
    return {"bookings": bookings, "total": len(bookings)}


@bookings_router.get("/")
async def list_bookings(
    status: Optional[str] = Query(None, description="One status or comma-separated list"),
    check_in_date: Optional[date] = Query(None),
    date_from: Optional[date] = Query(None, description="check-in on/after"),
    date_to: Optional[date] = Query(None, description="check-in on/before"),
    guest_phone: Optional[str] = Query(None),
    room_type: Optional[str] = Query(None),
    payment_status: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Search id / name / phone / email / room"),
    sort: str = Query("created_at", pattern="^(created_at|check_in_date|check_out_date)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    db = get_db()
    query: Dict[str, Any] = {}
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        query["status"] = statuses[0] if len(statuses) == 1 else {"$in": statuses}
    if check_in_date:
        query["check_in_date"] = str(check_in_date)
    elif date_from or date_to:
        rng: Dict[str, str] = {}
        if date_from:
            rng["$gte"] = str(date_from)
        if date_to:
            rng["$lte"] = str(date_to)
        query["check_in_date"] = rng
    if guest_phone:
        query["guest_phone"] = guest_phone
    if room_type:
        query["room_type"] = room_type
    if payment_status:
        query["payment_status"] = payment_status
    if source:
        query["source"] = source
    if q and q.strip():
        pat = {"$regex": re.escape(q.strip()), "$options": "i"}
        query["$or"] = [
            {"booking_id": pat}, {"guest_name": pat}, {"guest_phone": pat},
            {"guest_email": pat}, {"room_number": pat},
        ]

    skip = (page - 1) * limit
    direction = 1 if order == "asc" else -1
    total = await db.bookings.count_documents(query)
    docs = await (
        db.bookings.find(query).sort(sort, direction).skip(skip).limit(limit).to_list(limit)
    )
    return {
        "bookings": [_clean(d) for d in docs],
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit,
    }


@bookings_router.get("/{booking_id}")
async def get_booking(booking_id: str):
    db = get_db()
    doc = await db.bookings.find_one({"booking_id": booking_id.upper()})
    if not doc:
        raise HTTPException(status_code=404, detail="Booking not found")
    return _clean(doc)


@calendar_router.get("/{booking_id}/calendar.ics")
async def download_booking_calendar_ics(
    booking_id: str,
    token: str = Query(...),
):
    db = get_db()
    doc = await db.bookings.find_one({"booking_id": booking_id.upper()})
    if not doc:
        raise HTTPException(status_code=404, detail="Booking not found")

    expected = doc.get("calendar_token")
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid calendar token")

    profile = get_hotel_profile()
    ics_text = generate_booking_ics(
        booking_id=doc["booking_id"],
        guest_name=doc.get("guest_name", "Guest"),
        hotel_name=profile["name"],
        hotel_address=profile["address"],
        room_type=str(doc.get("room_type", "standard")),
        check_in_date=str(doc.get("check_in_date")),
        check_out_date=str(doc.get("check_out_date")),
        checkin_time=profile["checkin_time"],
        checkout_time=profile["checkout_time"],
        timezone_name=profile["timezone"],
    )
    filename = f"{doc['booking_id']}.ics"
    return Response(
        content=ics_text,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, max-age=3600",
        },
    )


@bookings_router.post("/")
async def create_booking_api(
    req: BookingCreateRequest,
    send_whatsapp: bool = Query(False, description="Also send the WhatsApp confirmation"),
):
    executor = get_tool_executor()
    db = get_db()

    room = await db.rooms.find_one({"_id": req.room_id})
    if not room:
        raise HTTPException(status_code=404, detail=f"Room {req.room_id} not found")

    result = await executor.create_booking(
        guest_name=req.guest_name,
        guest_phone=req.guest_phone,
        guest_email=req.guest_email,
        room_type=str(getattr(room.get("room_type"), "value", room.get("room_type", "standard"))),
        check_in_date=str(req.check_in_date),
        check_out_date=str(req.check_out_date),
        adults=req.adults,
        children=req.children,
        special_requests=req.special_requests,
        room_id=req.room_id,
        source="dashboard",
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Booking failed"))

    if send_whatsapp:
        wa = await executor.send_booking_confirmation_whatsapp(
            booking_id=result["booking_id"], whatsapp_number=req.guest_phone
        )
        result["whatsapp_sent"] = bool(wa.get("success"))
        if not wa.get("success"):
            result["whatsapp_error"] = wa.get("error")
    return result


class BookingUpdate(BaseModel):
    guest_name: Optional[str] = None
    guest_email: Optional[str] = None
    special_requests: Optional[str] = None
    payment_status: Optional[str] = None
    add_note: Optional[str] = None


@bookings_router.patch("/{booking_id}")
async def update_booking(booking_id: str, body: BookingUpdate):
    """Edit non-structural booking fields (dates/room changes: cancel and rebook)."""
    db = get_db()
    bid = booking_id.upper()
    sets: Dict[str, Any] = {}
    data = body.model_dump(exclude_unset=True)

    if "payment_status" in data:
        if data["payment_status"] not in PAYMENT_STATUSES:
            raise HTTPException(400, f"Invalid payment_status. Valid: {PAYMENT_STATUSES}")
        sets["payment_status"] = data["payment_status"]
    for field in ("guest_name", "guest_email", "special_requests"):
        if field in data:
            sets[field] = data[field]

    update: Dict[str, Any] = {}
    if sets:
        sets["updated_at"] = datetime.utcnow()
        update["$set"] = sets
    note = (data.get("add_note") or "").strip()
    if note:
        update["$push"] = {"booking_notes": f"{datetime.utcnow():%Y-%m-%d %H:%M} UTC — {note}"}
        update.setdefault("$set", {})["updated_at"] = datetime.utcnow()
    if not update:
        raise HTTPException(400, "Nothing to update")

    result = await db.bookings.update_one({"booking_id": bid}, update)
    if result.matched_count == 0:
        raise HTTPException(404, "Booking not found")
    doc = await db.bookings.find_one({"booking_id": bid})
    return _clean(doc)


@bookings_router.patch("/{booking_id}/status")
async def update_booking_status(booking_id: str, status: str):
    valid = [s.value for s in BookingStatus]
    if status not in valid:
        raise HTTPException(400, f"Invalid status. Valid: {valid}")

    # Cancelling goes through the cancellation-fee logic.
    if status == BookingStatus.CANCELLED.value:
        executor = get_tool_executor()
        result = await executor.cancel_booking(booking_id, "Cancelled from dashboard")
        if result.get("error"):
            raise HTTPException(400, result["error"])
        return result

    db = get_db()
    now = datetime.utcnow()
    sets: Dict[str, Any] = {"status": status, "updated_at": now}
    if status == BookingStatus.CHECKED_IN.value:
        sets["checked_in_at"] = now
    elif status == BookingStatus.CHECKED_OUT.value:
        sets["checked_out_at"] = now

    result = await db.bookings.update_one({"booking_id": booking_id.upper()}, {"$set": sets})
    if result.matched_count == 0:
        raise HTTPException(404, "Booking not found")
    return {"success": True, "booking_id": booking_id.upper(), "status": status}


@bookings_router.post("/{booking_id}/send-confirmation")
async def send_booking_confirmation(booking_id: str):
    """(Re)send the WhatsApp booking confirmation to the guest's phone."""
    db = get_db()
    doc = await db.bookings.find_one({"booking_id": booking_id.upper()})
    if not doc:
        raise HTTPException(404, "Booking not found")
    result = await get_tool_executor().send_booking_confirmation_whatsapp(
        booking_id=doc["booking_id"], whatsapp_number=doc.get("guest_phone", "")
    )
    if not result.get("success"):
        raise HTTPException(502, result.get("error", "WhatsApp send failed"))
    return result


@bookings_router.delete("/{booking_id}")
async def cancel_booking_api(booking_id: str, reason: Optional[str] = Query(None)):
    executor = get_tool_executor()
    result = await executor.cancel_booking(booking_id, reason)
    if result.get("error"):
        raise HTTPException(400, result["error"])
    return result


# ─────────────────────────────────────────────
# ROOMS API
# ─────────────────────────────────────────────

class RoomUpdate(BaseModel):
    room_number: Optional[str] = None
    room_type: Optional[RoomType] = None
    bed_type: Optional[BedType] = None
    floor: Optional[int] = None
    capacity: Optional[int] = None
    size_sqft: Optional[int] = None
    description: Optional[str] = None
    amenities: Optional[RoomAmenities] = None
    pricing: Optional[RoomPricing] = None
    images: Optional[List[str]] = None
    is_active: Optional[bool] = None
    is_maintenance: Optional[bool] = None


async def _ensure_no_upcoming_bookings(db, room_id: str) -> None:
    upcoming = await db.bookings.count_documents({
        "room_id": room_id,
        "status": {"$in": ACTIVE_BOOKING_STATUSES},
        "check_out_date": {"$gte": date.today().isoformat()},
    })
    if upcoming:
        raise HTTPException(
            409, f"Room has {upcoming} upcoming booking(s). Cancel or move them first."
        )


@rooms_router.get("/")
async def list_rooms(
    room_type: Optional[str] = None,
    is_active: bool = True,
    include_inactive: bool = False,
):
    db = get_db()
    query: Dict[str, Any] = {} if include_inactive else {"is_active": is_active}
    if room_type:
        query["room_type"] = room_type

    docs = await db.rooms.find(query).sort("room_number", 1).to_list(500)
    return {"rooms": [_clean(d) for d in docs], "total": len(docs)}


@rooms_router.post("/")
async def create_room(room: Room):
    db = get_db()
    existing = await db.rooms.find_one({"room_number": room.room_number})
    if existing:
        raise HTTPException(400, f"Room {room.room_number} already exists")

    doc = room.model_dump(by_alias=True)
    await db.rooms.insert_one(doc)
    return {"success": True, "room": _clean(doc)}


@rooms_router.get("/availability")
async def check_availability(
    check_in_date: date = Query(...),
    check_out_date: date = Query(...),
    room_type: Optional[str] = Query(None),
    adults: int = Query(1),
    children: int = Query(0),
):
    executor = get_tool_executor()
    return await executor.check_room_availability(
        check_in_date=str(check_in_date),
        check_out_date=str(check_out_date),
        room_type=room_type,
        adults=adults,
        children=children,
    )


@rooms_router.get("/{room_id}")
async def get_room(room_id: str):
    db = get_db()
    doc = await db.rooms.find_one({"_id": room_id})
    if not doc:
        raise HTTPException(404, "Room not found")
    return _clean(doc)


@rooms_router.patch("/{room_id}")
async def update_room(room_id: str, body: RoomUpdate):
    db = get_db()
    room = await db.rooms.find_one({"_id": room_id})
    if not room:
        raise HTTPException(404, "Room not found")

    sets = body.model_dump(exclude_unset=True)
    if not sets:
        raise HTTPException(400, "Nothing to update")

    new_number = sets.get("room_number")
    if new_number and new_number != room["room_number"]:
        if await db.rooms.find_one({"room_number": new_number, "_id": {"$ne": room_id}}):
            raise HTTPException(400, f"Room {new_number} already exists")

    if sets.get("is_active") is False and room.get("is_active", True):
        await _ensure_no_upcoming_bookings(db, room_id)

    sets["updated_at"] = datetime.utcnow()
    await db.rooms.update_one({"_id": room_id}, {"$set": sets})
    return _clean(await db.rooms.find_one({"_id": room_id}))


@rooms_router.delete("/{room_id}")
async def deactivate_room(room_id: str):
    """Soft delete: marks the room inactive. Blocked while it has upcoming bookings."""
    db = get_db()
    room = await db.rooms.find_one({"_id": room_id})
    if not room:
        raise HTTPException(404, "Room not found")

    await _ensure_no_upcoming_bookings(db, room_id)
    await db.rooms.update_one(
        {"_id": room_id}, {"$set": {"is_active": False, "updated_at": datetime.utcnow()}}
    )
    return {"success": True, "room_id": room_id, "is_active": False}
