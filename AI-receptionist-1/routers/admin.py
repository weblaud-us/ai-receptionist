"""
Admin / dashboard API  (prefix /admin)

Public:    POST /admin/login
Protected: everything else (Bearer JWT or X-Admin-Key) —
  GET  /admin/me
  GET  /admin/stats                 KPIs, charts, today's arrivals/departures
  GET  /admin/occupancy             room x day grid data ("tape chart")
  GET  /admin/conversations         transcripts list      (+ /{session_id})
  GET  /admin/guests                guest list            (+ /by-phone, PATCH /by-phone)
  GET  /admin/calls/live            calls in progress
  POST /admin/whatsapp/send         free-text WhatsApp to a guest
  GET  /admin/reminders             1-day reminder queue  (+ POST /reminders/run)
  GET  /admin/system                hotel profile, integrations, feature flags, DB status
"""

import logging
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from config import settings
from core.timeutils import business_today
from database.mongodb import get_db
from database.tenancy import get_hotel_profile
from utils.auth import (
    auth_configured, create_access_token, require_admin, verify_credentials,
)
from utils.serialize import to_jsonable

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/admin", tags=["Admin"])
protected = APIRouter(dependencies=[Depends(require_admin)])

ACTIVE_STATUSES = ["confirmed", "pending", "checked_in"]
REVENUE_STATUSES = ["confirmed", "pending", "checked_in", "checked_out"]


def _hotel_today() -> date:
    return business_today(settings.HOTEL_TIMEZONE)


def _strip_id(doc: Dict[str, Any]) -> Dict[str, Any]:
    doc.pop("_id", None)
    return to_jsonable(doc)


# ─────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


_failed_logins: Dict[str, List[float]] = defaultdict(list)
_MAX_FAILS, _FAIL_WINDOW = 5, 300  # 5 failures / 5 minutes per client IP


@admin_router.post("/login")
async def login(req: LoginRequest, request: Request):
    if not auth_configured():
        raise HTTPException(
            503,
            "Admin login is disabled. Set ADMIN_PASSWORD and a non-default SECRET_KEY in .env.",
        )
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    recent = [t for t in _failed_logins[ip] if now - t < _FAIL_WINDOW]
    _failed_logins[ip] = recent
    if len(recent) >= _MAX_FAILS:
        raise HTTPException(429, "Too many failed attempts. Try again in a few minutes.")

    if not verify_credentials(req.username, req.password):
        _failed_logins[ip].append(now)
        raise HTTPException(401, "Invalid username or password")

    _failed_logins.pop(ip, None)
    token, expires_in = create_access_token(req.username)
    return {"access_token": token, "token_type": "bearer", "expires_in": expires_in,
            "username": req.username}


@protected.get("/me")
async def me(admin: dict = Depends(require_admin)):
    return {"username": admin.get("sub"), "role": admin.get("role", "admin")}


# ─────────────────────────────────────────────
# STATS
# ─────────────────────────────────────────────

@protected.get("/stats")
async def stats(days: int = Query(30, ge=7, le=365)):
    db = get_db()
    today = _hotel_today()
    today_s = today.isoformat()
    since_dt = datetime.utcnow() - timedelta(days=days)

    # ── rooms / occupancy ────────────────────
    total_rooms = await db.rooms.count_documents({"is_active": True})
    maintenance = await db.rooms.count_documents({"is_active": True, "is_maintenance": True})
    sellable = max(total_rooms - maintenance, 0)

    occupied_ids = await db.bookings.distinct("room_id", {
        "status": {"$in": ACTIVE_STATUSES},
        "check_in_date": {"$lte": today_s},
        "check_out_date": {"$gt": today_s},
    })
    occupied = len(occupied_ids)
    occupancy_pct = round(occupied / sellable * 100, 1) if sellable else 0.0

    # ── today's movements ────────────────────
    arrivals = await db.bookings.find(
        {"check_in_date": today_s, "status": {"$in": ["confirmed", "pending"]}}
    ).sort("created_at", 1).to_list(100)
    departures = await db.bookings.find(
        {"check_out_date": today_s, "status": {"$in": ["checked_in", "confirmed"]}}
    ).sort("created_at", 1).to_list(100)
    in_house = await db.bookings.count_documents({"status": "checked_in"})

    # ── booking totals ───────────────────────
    by_status: Dict[str, int] = {}
    async for row in db.bookings.aggregate([{"$group": {"_id": "$status", "n": {"$sum": 1}}}]):
        by_status[str(row["_id"])] = row["n"]

    # ── period metrics (bookings created in window) ──
    period_match = {"created_at": {"$gte": since_dt}}
    revenue_match = {**period_match, "status": {"$in": REVENUE_STATUSES}}

    per_day: Dict[str, Dict[str, float]] = {}
    async for row in db.bookings.aggregate([
        {"$match": period_match},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
            "bookings": {"$sum": 1},
            "revenue": {"$sum": {"$cond": [
                {"$in": ["$status", REVENUE_STATUSES]}, "$pricing.final_total", 0]}},
        }},
    ]):
        per_day[row["_id"]] = {"bookings": row["bookings"], "revenue": row["revenue"] or 0}

    series = []
    for i in range(days - 1, -1, -1):
        d = (datetime.utcnow() - timedelta(days=i)).date().isoformat()
        v = per_day.get(d, {})
        series.append({"date": d, "bookings": v.get("bookings", 0),
                       "revenue": round(v.get("revenue", 0), 2)})

    async def _group(field: str, match: Dict[str, Any]) -> List[Dict[str, Any]]:
        out = []
        async for row in db.bookings.aggregate([
            {"$match": match},
            {"$group": {"_id": f"${field}", "count": {"$sum": 1},
                        "revenue": {"$sum": "$pricing.final_total"}}},
            {"$sort": {"count": -1}},
        ]):
            out.append({"name": str(row["_id"]), "count": row["count"],
                        "revenue": round(row["revenue"] or 0, 2)})
        return out

    by_source = await _group("source", revenue_match)
    by_room_type = await _group("room_type", revenue_match)

    revenue_total = sum(s["revenue"] for s in series)
    revenue_bookings = sum(r["count"] for r in by_room_type)
    bookings_period = sum(s["bookings"] for s in series)
    cancelled_period = await db.bookings.count_documents({**period_match, "status": "cancelled"})

    # ── upcoming check-ins, next 7 days ──────
    upcoming = []
    for i in range(7):
        d = (today + timedelta(days=i)).isoformat()
        n = await db.bookings.count_documents(
            {"check_in_date": d, "status": {"$in": ["confirmed", "pending"]}})
        upcoming.append({"date": d, "check_ins": n})

    # ── conversations / guests ───────────────
    conv_by_channel: Dict[str, int] = {}
    async for row in db.conversations.aggregate([
        {"$match": {"created_at": {"$gte": since_dt}}},
        {"$group": {"_id": "$channel", "n": {"$sum": 1}}},
    ]):
        conv_by_channel[str(row["_id"])] = row["n"]
    converted = await db.conversations.count_documents(
        {"created_at": {"$gte": since_dt}, "booking_ids.0": {"$exists": True}})
    conv_total = sum(conv_by_channel.values())

    try:
        from voice.twilio_handler import active_sessions
        live_calls = len(active_sessions)
    except Exception:
        live_calls = 0

    # ── needs attention ──────────────────────
    reminders_failed = await db.bookings.count_documents(
        {"status": "confirmed", "check_in_date": {"$gte": today_s},
         "one_day_reminder_error": {"$exists": True, "$ne": None},
         "one_day_reminder_sent": {"$ne": True}})
    pending_bookings = by_status.get("pending", 0)
    unpaid_upcoming = await db.bookings.count_documents(
        {"status": {"$in": ["confirmed", "checked_in"]}, "payment_status": "pending",
         "check_in_date": {"$lte": (today + timedelta(days=2)).isoformat()},
         "check_out_date": {"$gte": today_s}})

    def _mini(b: Dict[str, Any]) -> Dict[str, Any]:
        return to_jsonable({
            "booking_id": b.get("booking_id"), "guest_name": b.get("guest_name"),
            "guest_phone": b.get("guest_phone"), "room_number": b.get("room_number"),
            "room_type": b.get("room_type"), "status": b.get("status"),
            "check_in_date": b.get("check_in_date"), "check_out_date": b.get("check_out_date"),
            "payment_status": b.get("payment_status"),
            "total": (b.get("pricing") or {}).get("final_total"),
        })

    return {
        "as_of": to_jsonable(datetime.utcnow()),
        "hotel_today": today_s,
        "currency": settings.HOTEL_CURRENCY,
        "period_days": days,
        "rooms": {"total": total_rooms, "maintenance": maintenance, "sellable": sellable,
                  "occupied": occupied, "available": max(sellable - occupied, 0),
                  "occupancy_pct": occupancy_pct},
        "today": {"arrivals": [_mini(b) for b in arrivals],
                  "departures": [_mini(b) for b in departures], "in_house": in_house},
        "bookings": {"by_status": by_status, "period_total": bookings_period,
                     "period_cancelled": cancelled_period,
                     "cancellation_rate_pct": round(cancelled_period / bookings_period * 100, 1)
                     if bookings_period else 0.0,
                     "by_source": by_source, "by_room_type": by_room_type},
        "revenue": {"period_total": round(revenue_total, 2),
                    "avg_booking_value": round(revenue_total / max(revenue_bookings, 1), 2)},
        "series": series,
        "upcoming_check_ins": upcoming,
        "conversations": {"period_total": conv_total, "by_channel": conv_by_channel,
                          "converted": converted,
                          "conversion_pct": round(converted / conv_total * 100, 1) if conv_total else 0.0,
                          "live_calls": live_calls},
        "guests": {"total": await db.guests.count_documents({}),
                   "new_in_period": await db.guests.count_documents({"created_at": {"$gte": since_dt}})},
        "attention": {"reminders_failed": reminders_failed, "pending_bookings": pending_bookings,
                      "unpaid_soon": unpaid_upcoming},
    }


# ─────────────────────────────────────────────
# OCCUPANCY GRID
# ─────────────────────────────────────────────

@protected.get("/occupancy")
async def occupancy(
    start: Optional[date] = Query(None),
    days: int = Query(14, ge=1, le=62),
):
    db = get_db()
    hotel_today = _hotel_today()
    start_d = start or hotel_today
    end_d = start_d + timedelta(days=days)

    rooms = await db.rooms.find({"is_active": True}).sort("room_number", 1).to_list(500)
    bookings = await db.bookings.find({
        "status": {"$in": ACTIVE_STATUSES},
        "check_in_date": {"$lt": end_d.isoformat()},
        "check_out_date": {"$gt": start_d.isoformat()},
    }).to_list(5000)

    return {
        "today": hotel_today.isoformat(),
        "start": start_d.isoformat(),
        "end": end_d.isoformat(),
        "days": days,
        "rooms": [to_jsonable({
            "id": str(r["_id"]), "room_number": r.get("room_number"),
            "room_type": r.get("room_type"), "floor": r.get("floor"),
            "capacity": r.get("capacity"), "is_maintenance": r.get("is_maintenance", False),
        }) for r in rooms],
        "bookings": [to_jsonable({
            "booking_id": b.get("booking_id"), "room_id": b.get("room_id"),
            "guest_name": b.get("guest_name"), "status": b.get("status"),
            "check_in_date": b.get("check_in_date"), "check_out_date": b.get("check_out_date"),
        }) for b in bookings],
    }


# ─────────────────────────────────────────────
# CONVERSATIONS (voice + WhatsApp transcripts)
# ─────────────────────────────────────────────

@protected.get("/conversations")
async def list_conversations(
    channel: Optional[str] = Query(None, pattern="^(voice|whatsapp)$"),
    status: Optional[str] = Query(None, pattern="^(active|completed|abandoned)$"),
    has_booking: Optional[bool] = Query(None),
    q: Optional[str] = Query(None, description="phone or guest name"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    db = get_db()
    query: Dict[str, Any] = {}
    if channel:
        query["channel"] = channel
    if status:
        query["status"] = status
    if has_booking is True:
        query["booking_ids.0"] = {"$exists": True}
    elif has_booking is False:
        query["booking_ids.0"] = {"$exists": False}
    if q and q.strip():
        pat = {"$regex": re.escape(q.strip()), "$options": "i"}
        query["$or"] = [{"phone": pat}, {"context.guest_name": pat}]

    total = await db.conversations.count_documents(query)
    rows = []
    async for c in db.conversations.aggregate([
        {"$match": query},
        {"$sort": {"updated_at": -1}},
        {"$skip": (page - 1) * limit},
        {"$limit": limit},
        {"$project": {
            "_id": 0, "session_id": 1, "phone": 1, "channel": 1, "status": 1,
            "call_sid": 1, "created_at": 1, "updated_at": 1, "ended_at": 1,
            "duration_sec": 1, "booking_ids": 1,
            "guest_name": "$context.guest_name", "stage": "$context.conversation_stage",
            "message_count": {"$size": {"$ifNull": ["$messages", []]}},
            "last_message": {"$arrayElemAt": ["$messages", -1]},
        }},
    ]):
        lm = c.get("last_message") or {}
        c["last_message"] = {"role": lm.get("role"), "content": (lm.get("content") or "")[:160],
                             "timestamp": lm.get("timestamp")} if lm else None
        rows.append(to_jsonable(c))

    return {"conversations": rows, "total": total, "page": page,
            "pages": (total + limit - 1) // limit}


@protected.get("/conversations/{session_id}")
async def get_conversation(session_id: str):
    db = get_db()
    doc = await db.conversations.find_one({"session_id": session_id})
    if not doc:
        raise HTTPException(404, "Conversation not found")
    bookings = []
    if doc.get("booking_ids"):
        bookings = await db.bookings.find(
            {"booking_id": {"$in": doc["booking_ids"]}}).to_list(50)
    out = _strip_id(doc)
    out["bookings"] = [_strip_id(b) for b in bookings]
    return out


# ─────────────────────────────────────────────
# GUESTS
# ─────────────────────────────────────────────

VIP_TIERS = ["standard", "silver", "gold", "platinum"]


class GuestUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    nationality: Optional[str] = None
    vip_tier: Optional[str] = None
    notes: Optional[str] = None


@protected.get("/guests")
async def list_guests(
    q: Optional[str] = Query(None),
    vip_tier: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    db = get_db()
    query: Dict[str, Any] = {}
    if vip_tier:
        query["vip_tier"] = vip_tier
    if q and q.strip():
        pat = {"$regex": re.escape(q.strip()), "$options": "i"}
        query["$or"] = [{"phone": pat}, {"name": pat}, {"email": pat}]

    total = await db.guests.count_documents(query)
    docs = await (db.guests.find(query).sort("last_interaction", -1)
                  .skip((page - 1) * limit).limit(limit).to_list(limit))

    phones = [d.get("phone") for d in docs if d.get("phone")]
    counts: Dict[str, Dict[str, Any]] = {}
    if phones:
        async for row in db.bookings.aggregate([
            {"$match": {"guest_phone": {"$in": phones}}},
            {"$group": {"_id": "$guest_phone",
                        "bookings": {"$sum": 1},
                        "spent": {"$sum": {"$cond": [
                            {"$in": ["$status", REVENUE_STATUSES]}, "$pricing.final_total", 0]}}}},
        ]):
            counts[row["_id"]] = row

    guests = []
    for d in docs:
        c = counts.get(d.get("phone"), {})
        d["booking_count"] = c.get("bookings", 0)
        d["total_spent"] = round(c.get("spent", 0) or 0, 2)
        guests.append(_strip_id(d))
    return {"guests": guests, "total": total, "page": page, "pages": (total + limit - 1) // limit}


@protected.get("/guests/by-phone")
async def get_guest(phone: str = Query(..., min_length=3)):
    db = get_db()
    guest = await db.guests.find_one({"phone": phone})
    if not guest:
        raise HTTPException(404, "Guest not found")
    bookings = await db.bookings.find({"guest_phone": phone}).sort("created_at", -1).to_list(100)
    convs = []
    async for c in db.conversations.aggregate([
        {"$match": {"phone": phone}},
        {"$sort": {"updated_at": -1}}, {"$limit": 20},
        {"$project": {"_id": 0, "session_id": 1, "channel": 1, "status": 1, "created_at": 1,
                      "updated_at": 1, "duration_sec": 1, "booking_ids": 1,
                      "message_count": {"$size": {"$ifNull": ["$messages", []]}}}},
    ]):
        convs.append(to_jsonable(c))
    out = _strip_id(guest)
    out["bookings"] = [_strip_id(b) for b in bookings]
    out["conversations"] = convs
    return out


@protected.patch("/guests/by-phone")
async def update_guest(body: GuestUpdate, phone: str = Query(..., min_length=3)):
    data = body.model_dump(exclude_unset=True)
    if "vip_tier" in data and data["vip_tier"] not in VIP_TIERS:
        raise HTTPException(400, f"Invalid vip_tier. Valid: {VIP_TIERS}")
    if not data:
        raise HTTPException(400, "Nothing to update")
    data["updated_at"] = datetime.utcnow()

    db = get_db()
    res = await db.guests.update_one({"phone": phone}, {"$set": data})
    if res.matched_count == 0:
        raise HTTPException(404, "Guest not found")
    return _strip_id(await db.guests.find_one({"phone": phone}))


# ─────────────────────────────────────────────
# LIVE CALLS + MESSAGING
# ─────────────────────────────────────────────

@protected.get("/calls/live")
async def live_calls():
    try:
        from voice.twilio_handler import active_sessions
    except Exception as e:
        raise HTTPException(500, f"Voice module unavailable: {e}")
    now = time.time()
    calls = [{
        "call_sid": sid,
        "phone": s.get("phone"),
        "session_id": s.get("session_id"),
        "stream_connected": bool(s.get("websocket")),
        "ai_speaking": bool(s.get("ai_speaking")),
        "processing": bool(s.get("busy")),
        "seconds_since_speech": round(now - s.get("last_speech_ts", now)),
    } for sid, s in list(active_sessions.items())]
    return {"calls": calls, "total": len(calls)}


class WhatsAppSend(BaseModel):
    to: str = Field(min_length=6, max_length=32)
    message: str = Field(min_length=1, max_length=4000)


@protected.post("/whatsapp/send")
async def send_whatsapp(req: WhatsAppSend):
    """
    Free-text WhatsApp. Meta only delivers free-form text inside the 24-hour window
    after the guest last messaged the hotel.
    """
    from whatsapp.meta_handler import _send_meta_whatsapp, _normalize_wa_phone
    to = _normalize_wa_phone(re.sub(r"[\s\-()]", "", req.to))
    ok = await _send_meta_whatsapp(to, req.message, None)
    if not ok:
        raise HTTPException(502, "WhatsApp send failed (check Meta credentials, or the guest's "
                                 "24-hour messaging window has expired)")
    return {"success": True, "to": to}


# ─────────────────────────────────────────────
# REMINDERS
# ─────────────────────────────────────────────

@protected.get("/reminders")
async def reminders():
    db = get_db()
    today = _hotel_today()
    tomorrow_s = (today + timedelta(days=1)).isoformat()

    def _row(b: Dict[str, Any]) -> Dict[str, Any]:
        if b.get("one_day_reminder_sent"):
            state = "sent"
        elif b.get("one_day_reminder_error"):
            state = "failed"
        else:
            state = "pending"
        return to_jsonable({
            "booking_id": b.get("booking_id"), "guest_name": b.get("guest_name"),
            "guest_phone": b.get("guest_phone"), "room_number": b.get("room_number"),
            "check_in_date": b.get("check_in_date"), "state": state,
            "sent_at": b.get("one_day_reminder_sent_at"),
            "attempted_at": b.get("one_day_reminder_attempted_at"),
            "error": b.get("one_day_reminder_error"),
        })

    tomorrow = await db.bookings.find(
        {"status": "confirmed", "check_in_date": tomorrow_s}).to_list(200)
    failed = await db.bookings.find({
        "status": "confirmed", "check_in_date": {"$gte": today.isoformat()},
        "one_day_reminder_error": {"$exists": True, "$ne": None},
        "one_day_reminder_sent": {"$ne": True},
    }).to_list(100)
    recent_sent = await db.bookings.find({"one_day_reminder_sent": True}) \
        .sort("one_day_reminder_sent_at", -1).limit(20).to_list(20)

    return {
        "enabled": bool(settings.ENABLE_ONE_DAY_BOOKING_REMINDER and settings.ENABLE_WHATSAPP),
        "poll_minutes": settings.BOOKING_REMINDER_POLL_MINUTES,
        "reminder_for_date": tomorrow_s,
        "tomorrow": [_row(b) for b in tomorrow],
        "failed": [_row(b) for b in failed],
        "recent_sent": [_row(b) for b in recent_sent],
    }


@protected.post("/reminders/run")
async def run_reminders():
    from services.reminders import get_booking_reminder_service
    if not (settings.ENABLE_ONE_DAY_BOOKING_REMINDER and settings.ENABLE_WHATSAPP):
        raise HTTPException(400, "Reminders are disabled (ENABLE_ONE_DAY_BOOKING_REMINDER / ENABLE_WHATSAPP)")
    await get_booking_reminder_service().run_once()
    return {"success": True}


# ─────────────────────────────────────────────
# SYSTEM
# ─────────────────────────────────────────────

@protected.get("/system")
async def system():
    db = get_db()
    try:
        await db.command("ping")
        db_ok = True
    except Exception:
        db_ok = False

    def has(v: Optional[str]) -> bool:
        return bool((v or "").strip())

    profile = get_hotel_profile()
    return {
        "hotel": {k: profile[k] for k in (
            "name", "receptionist_name", "phone", "address",
            "checkin_time", "checkout_time", "currency", "timezone")},
        "environment": {"app_env": settings.APP_ENV, "debug": settings.DEBUG,
                        "sentry": bool(settings.SENTRY_DSN),
                        "llm_model": settings.OPENAI_MODEL, "db_name": settings.MONGODB_DB_NAME},
        "database": {"connected": db_ok},
        "integrations": {
            "openai": has(settings.OPENAI_API_KEY),
            "elevenlabs": has(settings.ELEVENLABS_API_KEY),
            "twilio_voice": has(settings.TWILIO_ACCOUNT_SID) and has(settings.TWILIO_AUTH_TOKEN)
                            and has(settings.TWILIO_PHONE_NUMBER),
            "meta_whatsapp": has(settings.META_WA_ACCESS_TOKEN) and has(settings.META_WA_PHONE_NUMBER_ID),
            "meta_webhook_verify_token": has(settings.META_WA_VERIFY_TOKEN),
            "meta_webhook_signature": has(settings.META_APP_SECRET),
            "twilio_signature_check": settings.VERIFY_TWILIO_SIGNATURE,
            "livekit": has(settings.LIVEKIT_URL) and has(settings.LIVEKIT_API_KEY)
                       and has(settings.LIVEKIT_API_SECRET),
            "public_base_url": has(settings.WEBHOOK_BASE_URL) or has(settings.CALL_BASE_URL),
        },
        "webhooks": {
            "twilio_voice": f"{(settings.WEBHOOK_BASE_URL or '').rstrip('/')}/voice/incoming",
            "twilio_status": f"{(settings.WEBHOOK_BASE_URL or '').rstrip('/')}/voice/status",
            "meta_whatsapp": f"{(settings.WEBHOOK_BASE_URL or '').rstrip('/')}/webhook",
        },
        "features": {
            "one_day_reminder": settings.ENABLE_ONE_DAY_BOOKING_REMINDER,
            "whatsapp": settings.ENABLE_WHATSAPP,
            "voice_calls": settings.ENABLE_VOICE_CALLS,
            "call_recording": settings.ENABLE_CALL_RECORDING,
            "livekit_realtime": settings.LIVEKIT_USE_REALTIME,
        },
        "memory": {"days": settings.CONVERSATION_MEMORY_DAYS,
                   "max_messages": settings.CONVERSATION_MEMORY_MAX_MESSAGES},
        "collections": {name: await db[name].estimated_document_count()
                        for name in ("rooms", "bookings", "guests", "conversations")} if db_ok else {},
    }


admin_router.include_router(protected)
