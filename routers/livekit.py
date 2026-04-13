"""
LiveKit call link endpoints.
GET  /call/{room_id}      → serves the browser call page (ui/call.html)
GET  /livekit/token       → issues a LiveKit JWT for the given room
POST /livekit/send-whatsapp → internal helper used by livekit_agent
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from config import settings
from database.tenancy import get_hotel_profile
from whatsapp.meta_handler import _send_meta_whatsapp

logger = logging.getLogger(__name__)

livekit_router = APIRouter(tags=["LiveKit"])


class LivekitWhatsAppSendRequest(BaseModel):
    to: str
    message: str


def _get_call_base_url() -> str:
    base = (settings.CALL_BASE_URL or "").strip()
    if not base:
        base = (settings.WEBHOOK_BASE_URL or "").strip()
    return base.rstrip("/")


@livekit_router.get("/call/{room_id}")
async def call_page(room_id: str):
    if not room_id:
        raise HTTPException(status_code=400, detail="Missing room id")
    return FileResponse("ui/call.html")


@livekit_router.get("/livekit/token")
async def livekit_token(
    room: str = Query(..., min_length=3),
    identity: Optional[str] = Query(default=None),
):
    if not settings.LIVEKIT_API_KEY or not settings.LIVEKIT_API_SECRET or not settings.LIVEKIT_URL:
        raise HTTPException(
            status_code=500,
            detail="LiveKit not configured. Set LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET in .env",
        )
    try:
        from livekit import api
    except ImportError as e:
        logger.error("LiveKit SDK import failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Install livekit-api: pip install livekit-api")

    identity = identity or f"guest_{uuid.uuid4().hex[:8]}"
    profile = get_hotel_profile()

    token = (
        api.AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_grants(api.VideoGrants(room_join=True, room=room))
        .to_jwt()
    )

    return JSONResponse({
        "token": token,
        "url": settings.LIVEKIT_URL,
        "room": room,
        "identity": identity,
        "call_url": f"{_get_call_base_url()}/call/{room}",
        "hotel_name": profile["name"],
        "receptionist_name": profile["receptionist_name"],
        "hotel_phone": profile["phone"],
    })


@livekit_router.post("/livekit/send-whatsapp")
async def livekit_send_whatsapp(req: LivekitWhatsAppSendRequest):
    """Internal: livekit_agent POSTs here to send WhatsApp confirmations."""
    ok = await _send_meta_whatsapp(req.to, req.message, None)
    if not ok:
        raise HTTPException(status_code=502, detail="WhatsApp send failed")
    return {"success": True}
