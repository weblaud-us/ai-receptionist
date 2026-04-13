"""
REST API Routers
- /bookings  — CRUD for hotel bookings
- /rooms     — Room management
"""

import logging
from datetime import date, datetime
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import JSONResponse

from database.mongodb import get_db
from database.models import (
    Room, Booking, BookingStatus, RoomType,
    BookingCreateRequest, AvailabilityRequest,
)
from ai.tools import get_tool_executor
from database.tenancy import get_hotel_profile
from services.calendar import generate_booking_ics

logger = logging.getLogger(__name__)

bookings_router = APIRouter(prefix="/bookings", tags=["Bookings"])
rooms_router = APIRouter(prefix="/rooms", tags=["Rooms"])

# ─────────────────────────────────────────────
# BOOKINGS API
# ─────────────────────────────────────────────

@bookings_router.get("/lookup/search")
async def lookup_booking(
    phone: Optional[str] = Query(None),
    booking_id: Optional[str] = Query(None),
    guest_name: Optional[str] = Query(None),
):
    """
    Lookup bookings by phone number, booking ID, or guest name.
    Used by voice agent to retrieve guest booking information.
    No hotel context required - searches across default hotel.
    """
    db = get_db()
    query = {}
    
    if booking_id:
        query["booking_id"] = booking_id.upper()
    elif phone:
        # Clean phone number for search
        clean_phone = phone.replace(" ", "").replace("-", "")
        if not clean_phone.startswith("+"):
            clean_phone = f"+{clean_phone}"
        # Search with and without + prefix
        query["$or"] = [
            {"guest_phone": clean_phone},
            {"guest_phone": clean_phone.lstrip("+")},
            {"guest_phone": {"$regex": clean_phone.lstrip("+").replace("+", ""), "$options": "i"}}
        ]
    elif guest_name:
        query["guest_name"] = {"$regex": guest_name, "$options": "i"}
    else:
        return {"bookings": [], "message": "Please provide phone, booking_id, or guest_name"}
    
    logger.info(f"🔍 Booking lookup query: {query}")
    
    docs = await db.bookings.find(query).sort("created_at", -1).limit(10).to_list(10)
    
    logger.info(f"🔍 Found {len(docs)} bookings")
    
    # Clean up and format response
    bookings = []
    for doc in docs:
        doc.pop("_id", None)
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
    status: Optional[str] = Query(None),
    check_in_date: Optional[date] = Query(None),
    guest_phone: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, le=100),
):
    db = get_db()
    query = {}
    if status:
        query["status"] = status
    if check_in_date:
        query["check_in_date"] = str(check_in_date)
    if guest_phone:
        query["guest_phone"] = guest_phone

    skip = (page - 1) * limit
    total = await db.bookings.count_documents(query)
    docs = await db.bookings.find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)

    # Clean up MongoDB ObjectId
    for doc in docs:
        doc.pop("_id", None)

    return {
        "bookings": docs,
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
    doc.pop("_id", None)
    return doc


@bookings_router.get("/{booking_id}/calendar.ics")
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
async def create_booking_api(req: BookingCreateRequest):
    executor = get_tool_executor()
    
    # Get room details to determine room_type
    db = get_db()
    room = await db.rooms.find_one({"_id": req.room_id})
    if not room:
        raise HTTPException(status_code=404, detail=f"Room {req.room_id} not found")
    
    room_type = room.get("room_type", "standard")
    
    result = await executor.create_booking(
        guest_name=req.guest_name,
        guest_phone=req.guest_phone,
        guest_email=req.guest_email,
        room_type=room_type,
        check_in_date=str(req.check_in_date),
        check_out_date=str(req.check_out_date),
        adults=req.adults,
        children=req.children,
        special_requests=req.special_requests,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Booking failed"))
    return result


@bookings_router.patch("/{booking_id}/status")
async def update_booking_status(booking_id: str, status: str):
    valid = [s.value for s in BookingStatus]
    if status not in valid:
        raise HTTPException(400, f"Invalid status. Valid: {valid}")

    db = get_db()
    result = await db.bookings.update_one(
        {"booking_id": booking_id.upper()},
        {"$set": {"status": status, "updated_at": datetime.utcnow()}}
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Booking not found")
    return {"success": True, "booking_id": booking_id, "status": status}


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

@rooms_router.get("/")
async def list_rooms(
    room_type: Optional[str] = None,
    is_active: bool = True,
):
    db = get_db()
    query: dict = {"is_active": is_active}
    if room_type:
        query["room_type"] = room_type

    docs = await db.rooms.find(query).sort("room_number", 1).to_list(200)
    for doc in docs:
        doc.pop("_id", None)
    return {"rooms": docs, "total": len(docs)}


@rooms_router.post("/")
async def create_room(room: Room):
    db = get_db()
    existing = await db.rooms.find_one({"room_number": room.room_number})
    if existing:
        raise HTTPException(400, f"Room {room.room_number} already exists")

    doc = room.model_dump(by_alias=True)
    await db.rooms.insert_one(doc)
    doc.pop("_id", None)
    return {"success": True, "room": doc}


@rooms_router.get("/availability")
async def check_availability(
    check_in_date: date = Query(...),
    check_out_date: date = Query(...),
    room_type: Optional[str] = Query(None),
    adults: int = Query(1),
    children: int = Query(0),
):
    executor = get_tool_executor()
    result = await executor.check_room_availability(
        check_in_date=str(check_in_date),
        check_out_date=str(check_out_date),
        room_type=room_type,
        adults=adults,
        children=children,
    )
    return result
