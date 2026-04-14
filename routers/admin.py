"""
Admin Dashboard API — Complete
Covers: dashboard stats, conversations (WhatsApp + Voice), handover,
        manager reply, block contact, mark resolved, bookings, settings, knowledge.
"""
import logging
from datetime import date, datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from database.mongodb import get_db
from database.tenancy import get_hotel_profile
from database.models import AgentSettings, KnowledgeItem, BookingStatus
from config import settings

logger = logging.getLogger(__name__)
admin_router = APIRouter(prefix="/admin", tags=["Admin Dashboard"])


# ── Request bodies ─────────────────────────────────────────────────────────

class HandoverRequest(BaseModel):
    active: bool
    note: Optional[str] = None

class ManagerReplyRequest(BaseModel):
    message: str

class BlockContactRequest(BaseModel):
    reason: Optional[str] = None

class AgentSettingsUpdate(BaseModel):
    greeting_message: Optional[str] = None
    personality: Optional[str] = None
    response_language: Optional[str] = None
    agent_active: Optional[bool] = None
    auto_booking_confirmation: Optional[bool] = None
    guest_reminders: Optional[bool] = None
    notify_on_escalation: Optional[bool] = None
    away_mode: Optional[bool] = None
    escalation_email: Optional[str] = None
    business_hours: Optional[dict] = None

class KnowledgeItemCreate(BaseModel):
    title: str
    content: str
    tags: List[str] = []

class KnowledgeItemUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[List[str]] = None
    is_active: Optional[bool] = None

class BookingConfirmRequest(BaseModel):
    send_whatsapp: bool = True

# ── Dashboard Stats ──────────────────────────────────────────────────────────

@admin_router.get("/dashboard")
async def dashboard_stats():
    db = get_db()
    today = str(date.today())
    first_day = date.today().replace(day=1)

    total_bookings  = await db.bookings.count_documents({})
    confirmed       = await db.bookings.count_documents({"status": "confirmed"})
    pending         = await db.bookings.count_documents({"status": "pending"})
    cancelled       = await db.bookings.count_documents({"status": "cancelled"})
    today_checkins  = await db.bookings.count_documents(
        {"check_in_date": today, "status": {"$in": ["confirmed", "checked_in"]}}
    )

    rev_pipeline = [
        {"$match": {"status": {"$in": ["confirmed", "checked_in", "checked_out"]},
                    "created_at": {"$gte": datetime(first_day.year, first_day.month, 1)}}},
        {"$group": {"_id": None, "total": {"$sum": "$pricing.final_total"}}},
    ]
    rev_result     = await db.bookings.aggregate(rev_pipeline).to_list(1)
    monthly_revenue = rev_result[0]["total"] if rev_result else 0

    total_rooms = await db.rooms.count_documents({"is_active": True})
    occupied    = await db.bookings.count_documents(
        {"status": "checked_in", "check_in_date": {"$lte": today}, "check_out_date": {"$gt": today}}
    )
    total_convs   = await db.conversations.count_documents({})
    active_convs  = await db.conversations.count_documents({"status": "active"})
    handover_convs = await db.conversations.count_documents({"handover_active": True})
    week_start    = datetime.utcnow() - timedelta(days=7)
    weekly_convs  = await db.conversations.count_documents({"created_at": {"$gte": week_start}})

    # channel breakdown
    wa_convs    = await db.conversations.count_documents({"channel": "whatsapp"})
    voice_convs = await db.conversations.count_documents({"channel": "voice"})

    profile = get_hotel_profile()
    return {
        "hotel_name": profile["name"],
        "receptionist_name": profile["receptionist_name"],
        "bookings": {
            "total": total_bookings, "confirmed": confirmed,
            "pending": pending, "cancelled": cancelled,
            "today_checkins": today_checkins,
        },
        "revenue": {"monthly": round(monthly_revenue, 2), "currency": profile["currency"]},
        "occupancy": {
            "total_rooms": total_rooms, "occupied": occupied,
            "rate_pct": round((occupied / total_rooms * 100) if total_rooms > 0 else 0, 1),
        },
        "conversations": {
            "total": total_convs, "active": active_convs,
            "handover": handover_convs, "this_week": weekly_convs,
            "whatsapp": wa_convs, "voice": voice_convs,
        },
    }

# ── Conversations / Inbox ────────────────────────────────────────────────────

@admin_router.get("/conversations")
async def list_conversations(
    status: Optional[str] = Query(None),
    handover: Optional[bool] = Query(None),
    channel: Optional[str] = Query(None),   # "whatsapp" | "voice" | None (all)
    page: int = Query(1, ge=1),
    limit: int = Query(50, le=200),
):
    """
    List all conversations (WhatsApp + Voice) for the inbox.
    Sorted newest first. Messages body excluded for performance.
    """
    db = get_db()
    query: dict = {}
    if status:
        query["status"] = status
    if handover is not None:
        query["handover_active"] = handover
    if channel:
        query["channel"] = channel

    skip  = (page - 1) * limit
    total = await db.conversations.count_documents(query)
    docs  = await db.conversations.find(
        query, {"messages": 0}
    ).sort("updated_at", -1).skip(skip).limit(limit).to_list(limit)

    # Enrich with unread count and last message preview
    for doc in docs:
        doc.pop("_id", None)
        # Resolve status label
        if doc.get("handover_active"):
            doc["inbox_status"] = "handover"
        elif doc.get("status") == "active":
            doc["inbox_status"] = "active"
        else:
            doc["inbox_status"] = "resolved"

    return {"conversations": docs, "total": total, "page": page,
            "pages": (total + limit - 1) // limit}


@admin_router.get("/conversations/{session_id}")
async def get_conversation(session_id: str):
    """
    Get full conversation with ALL messages (WhatsApp + Voice transcripts).
    Voice conversations have role=user (caller) and role=assistant (AI).
    """
    db = get_db()
    doc = await db.conversations.find_one({"session_id": session_id})
    if not doc:
        raise HTTPException(404, "Conversation not found")
    doc.pop("_id", None)
    return doc


@admin_router.post("/conversations/{session_id}/handover")
async def toggle_handover(session_id: str, req: HandoverRequest):
    """
    Toggle AI <-> Human handover.
    When active=True: AI stops responding; manager handles via /reply endpoint.
    When active=False: AI resumes handling.
    """
    db = get_db()
    conv = await db.conversations.find_one({"session_id": session_id})
    if not conv:
        raise HTTPException(404, "Conversation not found")

    update: dict = {"handover_active": req.active, "updated_at": datetime.utcnow()}
    if req.active:
        update["handover_at"]   = datetime.utcnow()
        update["handover_note"] = req.note
    else:
        update["handover_at"]   = None
        update["handover_note"] = None

    await db.conversations.update_one({"session_id": session_id}, {"$set": update})
    action = "taken over by manager" if req.active else "returned to AI"
    logger.info("Conversation %s %s", session_id, action)

    # Notify via WS broadcast
    try:
        from routers.admin_extras import ws_manager
        import asyncio
        asyncio.create_task(ws_manager.broadcast({
            "type": "handover_changed",
            "session_id": session_id,
            "handover_active": req.active,
        }))
    except Exception:
        pass

    return {"success": True, "handover_active": req.active, "session_id": session_id}


@admin_router.post("/conversations/{session_id}/reply")
async def manager_reply(session_id: str, req: ManagerReplyRequest):
    """
    Send a manual WhatsApp message from manager to the guest.
    Works for both WhatsApp and Voice conversation sessions.
    The message is saved to conversation history as [Manager] role.
    """
    db = get_db()
    conv = await db.conversations.find_one({"session_id": session_id})
    if not conv:
        raise HTTPException(404, "Conversation not found")

    phone = conv.get("phone", "")
    if not phone:
        raise HTTPException(400, "No phone number for this conversation")

    # Check if contact is blocked
    blocked = await db.blocked_contacts.find_one({"phone": phone})
    if blocked:
        raise HTTPException(403, f"Contact {phone} is blocked")

    from whatsapp.meta_handler import _send_meta_whatsapp
    ok = await _send_meta_whatsapp(phone, req.message, None)
    if not ok:
        raise HTTPException(502, "Failed to send WhatsApp message — check Meta token")

    manager_msg = {
        "role":         "assistant",
        "content":      f"[Manager] {req.message}",
        "timestamp":    datetime.utcnow(),
        "from_manager": True,
    }
    await db.conversations.update_one(
        {"session_id": session_id},
        {"$push": {"messages": manager_msg}, "$set": {"updated_at": datetime.utcnow()}}
    )

    # WS broadcast
    try:
        from routers.admin_extras import ws_manager
        import asyncio
        asyncio.create_task(ws_manager.broadcast({
            "type": "manager_reply_sent",
            "session_id": session_id,
            "message": req.message,
        }))
    except Exception:
        pass

    return {"success": True, "sent_to": phone}


@admin_router.post("/conversations/{session_id}/mark-read")
async def mark_read(session_id: str):
    db = get_db()
    await db.conversations.update_one(
        {"session_id": session_id},
        {"$set": {"unread_count": 0, "updated_at": datetime.utcnow()}}
    )
    return {"success": True}


@admin_router.post("/conversations/{session_id}/resolve")
async def resolve_conversation(session_id: str):
    """Mark conversation as resolved (AI stays, just changes status label)."""
    db = get_db()
    conv = await db.conversations.find_one({"session_id": session_id})
    if not conv:
        raise HTTPException(404, "Conversation not found")
    await db.conversations.update_one(
        {"session_id": session_id},
        {"$set": {
            "status":         "completed",
            "handover_active": False,
            "updated_at":     datetime.utcnow(),
        }}
    )
    return {"success": True, "session_id": session_id, "status": "resolved"}

# ── Block Contact ─────────────────────────────────────────────────────────────

@admin_router.post("/conversations/{session_id}/block")
async def block_contact(session_id: str, req: BlockContactRequest):
    """
    Block a contact by phone number.
    - Saves phone to blocked_contacts collection
    - WhatsApp & voice handlers will ignore messages from blocked numbers
    - All conversations from this phone are marked blocked
    """
    db = get_db()
    conv = await db.conversations.find_one({"session_id": session_id})
    if not conv:
        raise HTTPException(404, "Conversation not found")

    phone = conv.get("phone", "")
    if not phone:
        raise HTTPException(400, "No phone number in this conversation")

    # Upsert into blocked_contacts
    await db.blocked_contacts.update_one(
        {"phone": phone},
        {"$set": {
            "phone":      phone,
            "reason":     req.reason or "Blocked by manager",
            "blocked_at": datetime.utcnow(),
            "blocked_by": "manager",
        }},
        upsert=True,
    )

    # Mark all conversations from this phone as blocked
    await db.conversations.update_many(
        {"phone": phone},
        {"$set": {"is_blocked": True, "updated_at": datetime.utcnow()}}
    )

    logger.info("🚫 Contact blocked: %s  reason=%s", phone, req.reason)
    return {"success": True, "phone": phone, "blocked": True}


@admin_router.delete("/conversations/{session_id}/block")
async def unblock_contact(session_id: str):
    """Unblock a contact — AI will resume responding."""
    db = get_db()
    conv = await db.conversations.find_one({"session_id": session_id})
    if not conv:
        raise HTTPException(404, "Conversation not found")

    phone = conv.get("phone", "")
    await db.blocked_contacts.delete_one({"phone": phone})
    await db.conversations.update_many(
        {"phone": phone},
        {"$set": {"is_blocked": False, "updated_at": datetime.utcnow()}}
    )
    logger.info("✅ Contact unblocked: %s", phone)
    return {"success": True, "phone": phone, "blocked": False}


@admin_router.get("/blocked-contacts")
async def list_blocked_contacts():
    """List all blocked contacts."""
    db = get_db()
    docs = await db.blocked_contacts.find({}).sort("blocked_at", -1).to_list(500)
    for d in docs:
        d.pop("_id", None)
    return {"blocked": docs, "total": len(docs)}


# ── Bookings (Admin) ──────────────────────────────────────────────────────────

@admin_router.get("/bookings")
async def admin_list_bookings(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, le=100),
):
    db = get_db()
    query: dict = {}
    if status and status != "all":
        query["status"] = status
    if search:
        query["$or"] = [
            {"guest_name":  {"$regex": search, "$options": "i"}},
            {"booking_id":  {"$regex": search, "$options": "i"}},
            {"guest_phone": {"$regex": search, "$options": "i"}},
        ]
    skip  = (page - 1) * limit
    total = await db.bookings.count_documents(query)
    docs  = await db.bookings.find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    for doc in docs:
        doc.pop("_id", None)
    return {"bookings": docs, "total": total, "page": page, "pages": (total + limit - 1) // limit}


@admin_router.get("/bookings/{booking_id}")
async def admin_get_booking(booking_id: str):
    db = get_db()
    doc = await db.bookings.find_one({"booking_id": booking_id.upper()})
    if not doc:
        raise HTTPException(404, "Booking not found")
    doc.pop("_id", None)
    return doc


@admin_router.post("/bookings/{booking_id}/confirm")
async def admin_confirm_booking(booking_id: str, req: BookingConfirmRequest):
    db = get_db()
    booking = await db.bookings.find_one({"booking_id": booking_id.upper()})
    if not booking:
        raise HTTPException(404, "Booking not found")
    await db.bookings.update_one(
        {"booking_id": booking_id.upper()},
        {"$set": {"status": "confirmed", "updated_at": datetime.utcnow()}}
    )
    if req.send_whatsapp:
        from whatsapp.meta_handler import _send_meta_whatsapp
        profile = get_hotel_profile()
        msg = (
            f"✅ Sir, your booking *{booking_id.upper()}* has been confirmed!\n"
            f"Room: {str(booking.get('room_type','')).title()} | "
            f"Check-in: {booking.get('check_in_date')} at {profile['checkin_time']}\n"
            f"We look forward to welcoming you!"
        )
        await _send_meta_whatsapp(booking.get("guest_phone", ""), msg, None)
    return {"success": True, "booking_id": booking_id.upper(), "status": "confirmed"}


@admin_router.post("/bookings/{booking_id}/cancel")
async def admin_cancel_booking(booking_id: str, reason: Optional[str] = Query(None)):
    from ai.tools import get_tool_executor
    executor = get_tool_executor()
    result   = await executor.cancel_booking(booking_id, reason)
    if result.get("error"):
        raise HTTPException(400, result["error"])
    return result

# ── Agent Settings ────────────────────────────────────────────────────────────

async def _get_or_create_settings(db) -> dict:
    doc = await db.agent_settings.find_one({"_id": "singleton"})
    if not doc:
        defaults = AgentSettings()
        doc = defaults.model_dump(by_alias=True)
        await db.agent_settings.insert_one(doc)
    doc.pop("_id", None)
    return doc


@admin_router.get("/settings")
async def get_agent_settings():
    db = get_db()
    return await _get_or_create_settings(db)


@admin_router.post("/settings")
async def save_agent_settings(req: AgentSettingsUpdate):
    db = get_db()
    update = {k: v for k, v in req.model_dump().items() if v is not None}
    if not update:
        raise HTTPException(400, "No fields to update")
    update["updated_at"] = datetime.utcnow()
    await db.agent_settings.update_one(
        {"_id": "singleton"}, {"$set": update}, upsert=True,
    )
    return await _get_or_create_settings(db)


# ── Knowledge Base ────────────────────────────────────────────────────────────

@admin_router.get("/knowledge")
async def list_knowledge(is_active: Optional[bool] = Query(None)):
    db = get_db()
    query = {}
    if is_active is not None:
        query["is_active"] = is_active
    docs = await db.knowledge_items.find(query).sort("created_at", -1).to_list(200)
    total_queries = sum(d.get("query_count", 0) for d in docs)
    for doc in docs:
        doc.pop("_id", None)
    return {"items": docs, "total": len(docs), "total_queries": total_queries}


@admin_router.post("/knowledge")
async def add_knowledge(req: KnowledgeItemCreate):
    db = get_db()
    item = KnowledgeItem(title=req.title, content=req.content, tags=req.tags)
    doc  = item.model_dump(by_alias=True)
    await db.knowledge_items.insert_one(doc)
    doc.pop("_id", None)
    return {"success": True, "item": doc}


@admin_router.put("/knowledge/{item_id}")
async def update_knowledge(item_id: str, req: KnowledgeItemUpdate):
    db = get_db()
    update = {k: v for k, v in req.model_dump().items() if v is not None}
    if not update:
        raise HTTPException(400, "Nothing to update")
    update["updated_at"] = datetime.utcnow()
    result = await db.knowledge_items.update_one({"_id": item_id}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(404, "Knowledge item not found")
    return {"success": True}


@admin_router.delete("/knowledge/{item_id}")
async def delete_knowledge(item_id: str):
    db = get_db()
    result = await db.knowledge_items.delete_one({"_id": item_id})
    if result.deleted_count == 0:
        raise HTTPException(404, "Knowledge item not found")
    return {"success": True}
