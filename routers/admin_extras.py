"""
Admin Dashboard WebSocket + extra REST endpoints
- Real-time conversation updates via WebSocket
- CSV export for bookings
- Conversation inbox analytics
- Knowledge base AI test
Register this router in main.py alongside admin_router.
"""
import csv
import io
import json
import logging
from datetime import datetime, date, timedelta
from typing import Optional, Set
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import StreamingResponse

from database.mongodb import get_db
from database.tenancy import get_hotel_profile

logger = logging.getLogger(__name__)
admin_extras_router = APIRouter(prefix="/admin", tags=["Admin Extras"])

# ─────────────────────────────────────────────
# WebSocket connection manager
# ─────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.add(ws)
        logger.info("Dashboard WS connected (%d total)", len(self.active))

    def disconnect(self, ws: WebSocket):
        self.active.discard(ws)
        logger.info("Dashboard WS disconnected (%d remain)", len(self.active))

    async def broadcast(self, data: dict):
        dead = set()
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.add(ws)
        self.active -= dead

ws_manager = ConnectionManager()


@admin_extras_router.websocket("/ws")
async def dashboard_websocket(websocket: WebSocket):
    """
    WebSocket endpoint for real-time dashboard updates.
    Clients connect to ws://host/admin/ws and receive push events:
      { "type": "new_message",  "session_id": "...", "preview": "..." }
      { "type": "new_booking",  "booking_id": "...", "status": "..." }
      { "type": "stats_update", "bookings": {...}, "convs": {...} }
    """
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep-alive: client can send ping, we pong back
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


async def broadcast_new_message(session_id: str, phone: str, preview: str, label: Optional[str] = None):
    """Call this from WhatsApp handlers after saving a new inbound message."""
    await ws_manager.broadcast({
        "type": "new_message",
        "session_id": session_id,
        "phone": phone,
        "preview": preview,
        "label": label,
        "timestamp": datetime.utcnow().isoformat(),
    })


async def broadcast_new_booking(booking_id: str, guest_name: str, status: str, amount: float):
    """Call this from booking creation code."""
    await ws_manager.broadcast({
        "type": "new_booking",
        "booking_id": booking_id,
        "guest_name": guest_name,
        "status": status,
        "amount": amount,
        "timestamp": datetime.utcnow().isoformat(),
    })


# ─────────────────────────────────────────────
# CSV Export for Bookings
# ─────────────────────────────────────────────

@admin_extras_router.get("/bookings/export-csv")
async def export_bookings_csv(
    status: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
):
    """Export all bookings (or filtered) as CSV download."""
    db = get_db()
    query: dict = {}
    if status and status != "all":
        query["status"] = status
    if from_date:
        query["check_in_date"] = {"$gte": str(from_date)}
    if to_date:
        query.setdefault("check_in_date", {})
        query["check_in_date"]["$lte"] = str(to_date)

    docs = await db.bookings.find(query).sort("created_at", -1).to_list(5000)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Booking ID", "Guest Name", "Phone", "Email",
        "Room Type", "Room Number", "Check-In", "Check-Out",
        "Nights", "Adults", "Children", "Amount", "Currency",
        "Status", "Source", "Created At",
    ])
    for b in docs:
        p = b.get("pricing", {})
        writer.writerow([
            b.get("booking_id", ""),
            b.get("guest_name", ""),
            b.get("guest_phone", ""),
            b.get("guest_email", ""),
            b.get("room_type", ""),
            b.get("room_number", ""),
            b.get("check_in_date", ""),
            b.get("check_out_date", ""),
            p.get("nights", ""),
            b.get("adults", 1),
            b.get("children", 0),
            p.get("final_total", ""),
            p.get("currency", ""),
            b.get("status", ""),
            b.get("source", ""),
            b.get("created_at", ""),
        ])

    output.seek(0)
    filename = f"bookings_{date.today()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─────────────────────────────────────────────
# Conversation Inbox Analytics
# ─────────────────────────────────────────────

@admin_extras_router.get("/conversations/analytics")
async def conversation_analytics(days: int = Query(7, ge=1, le=90)):
    """Daily conversation volume for the last N days."""
    db = get_db()
    since = datetime.utcnow() - timedelta(days=days)
    pipeline = [
        {"$match": {"created_at": {"$gte": since}}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
            "count": {"$sum": 1},
            "bookings": {"$sum": {"$size": {"$ifNull": ["$booking_ids", []]}}},
        }},
        {"$sort": {"_id": 1}},
    ]
    result = await db.conversations.aggregate(pipeline).to_list(days + 1)
    return {"days": days, "data": result}


# ─────────────────────────────────────────────
# Knowledge Base AI Test
# ─────────────────────────────────────────────

@admin_extras_router.post("/knowledge/test")
async def test_knowledge_query(question: str = Query(...)):
    """
    Search knowledge base for relevant content and return a mock AI answer.
    Used by the Knowledge Base test preview widget.
    """
    db = get_db()
    # Simple keyword search across knowledge items
    q_words = [w for w in question.lower().split() if len(w) > 2]
    if not q_words:
        return {"answer": "Please ask a specific question.", "matched_item": None}

    docs = await db.knowledge_items.find({"is_active": True}).to_list(200)
    best_score = 0
    best_item = None
    for doc in docs:
        combined = (doc.get("title", "") + " " + doc.get("content", "")).lower()
        score = sum(1 for w in q_words if w in combined)
        if score > best_score:
            best_score = score
            best_item = doc

    if best_item and best_score > 0:
        # Increment query count
        await db.knowledge_items.update_one(
            {"_id": best_item["_id"]},
            {"$inc": {"query_count": 1}},
        )
        snippet = best_item.get("content", "")[:400]
        answer = f"{snippet}{'…' if len(best_item.get('content','')) > 400 else ''}"
    else:
        answer = "I don't have specific information about that yet. Please contact our front desk for assistance."

    return {
        "answer": answer,
        "matched_item": best_item.get("title") if best_item else None,
        "confidence": round(best_score / max(len(q_words), 1), 2),
    }


# ─────────────────────────────────────────────
# Booking Revenue Chart Data
# ─────────────────────────────────────────────

@admin_extras_router.get("/bookings/revenue-chart")
async def revenue_chart(days: int = Query(30, ge=7, le=365)):
    """Daily revenue for the last N days for charting."""
    db = get_db()
    since = datetime.utcnow() - timedelta(days=days)
    pipeline = [
        {"$match": {
            "status": {"$in": ["confirmed", "checked_in", "checked_out"]},
            "created_at": {"$gte": since},
        }},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
            "revenue": {"$sum": "$pricing.final_total"},
            "bookings": {"$sum": 1},
        }},
        {"$sort": {"_id": 1}},
    ]
    result = await db.bookings.aggregate(pipeline).to_list(days + 1)
    profile = get_hotel_profile()
    return {"days": days, "currency": profile["currency"], "data": result}
