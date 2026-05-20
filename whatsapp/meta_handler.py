"""
Meta WhatsApp Cloud API webhook handler.
Receives inbound messages and sends AI replies via Graph API.

Token-cost optimisations applied:
- Off-topic (non-hotel) messages get a lightweight canned reply — no agent call.
- Quick greetings bypass agent and return a static welcome.
- History trimmed to 8 messages (down from 12) via agent.py.
- max_tokens reduced to 400 for WhatsApp in agent.py.
"""

import asyncio
import json
import logging
import time
import os
import tempfile
from collections import OrderedDict
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

from ai.agent import get_whatsapp_agent
from ai.prompts import GREETING_WHATSAPP, BOOKING_CONFIRMATION_WHATSAPP
from config import settings
from database.mongodb import get_db
from database.tenancy import get_hotel_profile
from whatsapp.handler import _get_or_create_whatsapp_session
from voice.stt_tts import get_stt, get_tts

logger = logging.getLogger(__name__)
router = APIRouter(tags=["WhatsApp Meta"])

_recent_meta_message_ids: "OrderedDict[str, float]" = OrderedDict()
_META_DEDUP_TTL_SECONDS = 10 * 60
_META_DEDUP_MAX = 5000


def _seen_meta_message_recently(message_id: Optional[str]) -> bool:
    if not message_id:
        return False
    now = time.time()
    cutoff = now - _META_DEDUP_TTL_SECONDS
    while _recent_meta_message_ids:
        _, ts = next(iter(_recent_meta_message_ids.items()))
        if ts >= cutoff:
            break
        _recent_meta_message_ids.popitem(last=False)
    if message_id in _recent_meta_message_ids:
        return True
    _recent_meta_message_ids[message_id] = now
    if len(_recent_meta_message_ids) > _META_DEDUP_MAX:
        _recent_meta_message_ids.popitem(last=False)
    return False


def _get_meta_base_url() -> str:
    version = (settings.META_GRAPH_API_VERSION or "v20.0").strip()
    return f"https://graph.facebook.com/{version}"


def _get_meta_access_token() -> str:
    return (settings.META_WA_ACCESS_TOKEN or "").strip()


def _normalize_wa_phone(value: Optional[str]) -> str:
    if not value:
        return ""
    if value.startswith("+"):
        return value
    if value.isdigit():
        return f"+{value}"
    return value


def _get_call_base_url() -> str:
    base = (settings.CALL_BASE_URL or "").strip()
    if not base:
        base = (settings.WEBHOOK_BASE_URL or "").strip()
    return base.rstrip("/")


def _is_call_request(text: str) -> bool:
    if not text:
        return False
    norm = text.lower()
    keywords = [
        "voice call", "audio call", "phone call",
        "want to call", "want call", "i want call",
        "can i call", "give me a call", "let me call",
        "call you", "call me", "wanna call",
        "speak to", "speak with", "talk to you",
    ]
    bangla = ["\u0995\u09b2", "\u0995\u09a5\u09be \u09ac\u09b2", "\u0995\u09b2 \u09a6\u09bf\u09a4\u09c7", "\u09ab\u09cb\u09a8 \u0995\u09b0\u09a4\u09c7", "\u09ab\u09cb\u09a8 \u09a8\u09ae\u09cd\u09ac\u09b0", "\u0995\u09b2 \u0995\u09b0\u09ac", "\u0995\u09a5\u09be \u09ac\u09b2\u09a4\u09c7"]
    return any(k in norm for k in keywords) or any(k in norm for k in bangla)


_QUICK_GREETING_WORDS = {
    "hi", "hello", "hey", "start", "menu", "helo", "hii", "hiii",
    "good morning", "good afternoon", "good evening", "good night",
    "greetings", "salaam", "salam",
}

_RETURNING_GUEST_REPLIES = [
    "Hello! \U0001f60a How can I help you today?",
    "Good to hear from you! How may I assist you?",
    "Hi there! What can I help you with today?",
    "Welcome back! How may I assist you?",
    "Hello! What can I do for you today?",
]


def _is_quick_greeting(text: str) -> bool:
    norm = (text or "").strip().lower()
    return norm in _QUICK_GREETING_WORDS


# ── Off-topic / non-hotel question detection ─────────────────────────────────
_HOTEL_KEYWORDS = {
    "book", "room", "reserv", "check-in", "checkin", "check in",
    "check-out", "checkout", "check out", "cancel", "price", "rate",
    "availab", "suite", "hotel", "stay", "night", "guest", "payment",
    "invoice", "receipt", "breakfast", "pool", "gym", "parking", "wifi",
    "amenity", "service", "refund", "reschedule", "confirm", "booking id",
    "floor", "bed", "twin", "double", "single", "deluxe", "presidential",
    "executive", "standard", "arrive", "depart", "reception", "lobby",
    "facility", "facilities", "restaurant", "spa", "laundry", "housekeep",
    # Bangla hotel-related
    "\u0986\u09ae\u09be\u09b0", "\u09ac\u09c1\u0995", "\u09b0\u09c1\u09ae",
    "\u09b9\u09cb\u099f\u09c7\u09b2", "\u09b0\u09bf\u099c\u09be\u09b0\u09cd\u09ad",
    "\u099a\u09c7\u0995 \u0987\u09a8", "\u099a\u09c7\u0995 \u0986\u0989\u099f",
    "\u09ae\u09c2\u09b2\u09cd\u09af", "\u09b0\u09c7\u099f",
}

_OFF_TOPIC_REPLIES = [
    "Sorry sir, I am the hotel receptionist and can only assist with hotel-related queries — room bookings, availability, pricing, or reservations. May I help you with any of those?",
    "Apologies sir, that's outside my area. I'm here for hotel bookings, room availability, pricing, and reservation queries. Is there anything about your stay I can help with?",
    "Sorry sir, I'm the hotel receptionist so I can only help with hotel-related questions. Feel free to ask about room bookings, pricing, or availability!",
]


def _is_off_topic(text: str) -> bool:
    """Return True when the message has NO hotel-related keywords → skip full agent."""
    if not text or len(text.strip()) < 4:
        return False
    if _is_quick_greeting(text):
        return False  # greetings are handled separately, never off-topic
    norm = text.lower()
    return not any(kw in norm for kw in _HOTEL_KEYWORDS)


def _build_call_options_message(profile: dict, room_id: str) -> str:
    """Build message offering both Twilio PSTN and LiveKit browser call options."""
    twilio_number = (profile.get("twilio_voice_number") or settings.TWILIO_PHONE_NUMBER or "").strip()
    base = _get_call_base_url()

    lines = [
        f"You can speak with our receptionist *{profile.get('receptionist_name', 'Aria')}* "
        f"from *{profile.get('name', 'the hotel')}* in two ways:\n"
    ]
    if twilio_number:
        lines.append(f"\ud83d\udcde *Real Phone Call:*\nCall us directly at *{twilio_number}*")
    if base:
        call_link = f"{base}/call/{room_id}"
        lines.append(f"\ud83c\udf10 *Browser Call (no phone needed):*\nTap this link:\n{call_link}")

    if not twilio_number and not base:
        return "Sorry, call options are not configured yet. Please ask your query here."

    lines.append(
        "\nBoth options connect you live with our receptionist who can "
        "take bookings, check availability, and answer all your questions. "
        "We look forward to speaking with you! \ud83c\udfa4"
    )
    return "\n\n".join(lines)


def _chunk_message(message: str, chunk_size: int = 1500) -> list[str]:
    if not message:
        return [""]
    msg = message.strip()
    if len(msg) <= chunk_size:
        return [msg]
    chunks: list[str] = []
    paragraphs = msg.split("\n")
    current = ""
    for para in paragraphs:
        candidate = para.strip()
        if not candidate:
            if current and len(current) + 1 <= chunk_size:
                current += "\n"
            continue
        addition = candidate if not current else f"{current}\n{candidate}"
        if len(addition) <= chunk_size:
            current = addition
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(candidate) > chunk_size:
            chunks.append(candidate[:chunk_size])
            candidate = candidate[chunk_size:]
        current = candidate
    if current:
        chunks.append(current)
    return chunks or [msg[:chunk_size]]


def _guess_audio_suffix(mime_type: Optional[str]) -> str:
    if not mime_type:
        return ".ogg"
    mt = mime_type.lower()
    if "ogg" in mt:
        return ".ogg"
    if "opus" in mt:
        return ".opus"
    if "mpeg" in mt or "mp3" in mt:
        return ".mp3"
    if "wav" in mt:
        return ".wav"
    if "m4a" in mt or "mp4" in mt:
        return ".m4a"
    return ".ogg"


async def _download_meta_media(media_id: str) -> tuple[Optional[bytes], Optional[str]]:
    """Download WhatsApp media bytes and return (bytes, mime_type)."""
    token = _get_meta_access_token()
    if not token:
        logger.warning("Meta media download skipped: access token missing")
        return None, None
    if not media_id:
        return None, None

    base = _get_meta_base_url()
    headers = {"Authorization": f"Bearer {token}"}

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            meta_url = f"{base}/{media_id}"
            meta_resp = await client.get(meta_url, headers=headers)
            if meta_resp.status_code >= 400:
                logger.error("Meta media metadata failed: %s %s", meta_resp.status_code, meta_resp.text)
                return None, None
            meta = meta_resp.json()
            media_url = meta.get("url")
            mime_type = meta.get("mime_type")
            if not media_url:
                logger.error("Meta media metadata missing url for id=%s", media_id)
                return None, None
            media_resp = await client.get(media_url, headers=headers)
            if media_resp.status_code >= 400:
                logger.error("Meta media download failed: %s %s", media_resp.status_code, media_resp.text)
                return None, None
            return media_resp.content, mime_type
    except Exception as e:
        logger.error("Meta media download error: %s", e, exc_info=True)
        return None, None


async def _transcribe_audio_bytes(audio_bytes: bytes, mime_type: Optional[str]) -> str:
    if not audio_bytes:
        return ""
    if settings.STT_PROVIDER == "openai_whisper":
        if not settings.OPENAI_API_KEY:
            logger.warning("OpenAI Whisper skipped: OPENAI_API_KEY missing")
            return ""
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
            suffix = _guess_audio_suffix(mime_type)
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(audio_bytes)
                tmp_path = f.name
            try:
                with open(tmp_path, "rb") as audio_file:
                    transcript = await client.audio.transcriptions.create(
                        model="whisper-1", file=audio_file, response_format="text",
                    )
                text = (transcript or "").strip()
                logger.info("Whisper transcript length: %s", len(text))
                return text
            finally:
                os.unlink(tmp_path)
        except Exception as e:
            logger.error("OpenAI Whisper transcription failed: %s", e, exc_info=True)
            return ""
    try:
        stt = get_stt()
        text = (await stt.transcribe(audio_bytes)) or ""
        return text
    except Exception as e:
        logger.error("STT transcription failed: %s", e, exc_info=True)
        return ""


async def _synthesize_tts_mp3(text: str) -> Optional[bytes]:
    if not text:
        return None
    try:
        tts = get_tts()
        return await tts.synthesize(text)
    except Exception as e:
        logger.error("TTS synthesis failed: %s", e, exc_info=True)
        if settings.TTS_PROVIDER == "elevenlabs" and settings.OPENAI_API_KEY:
            try:
                from openai import AsyncOpenAI
                client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
                response = await client.audio.speech.create(
                    model=settings.OPENAI_TTS_MODEL, voice=settings.OPENAI_TTS_VOICE,
                    input=text, response_format="mp3",
                )
                return response.content
            except Exception as e2:
                logger.error("OpenAI TTS fallback failed: %s", e2, exc_info=True)
        return None


async def _upload_meta_media(audio_bytes: bytes, mime_type: str = "audio/mpeg") -> Optional[str]:
    token = _get_meta_access_token()
    if not token:
        return None
    pnid = settings.META_WA_PHONE_NUMBER_ID
    if not pnid:
        return None
    url = f"{_get_meta_base_url()}/{pnid}/media"
    headers = {"Authorization": f"Bearer {token}"}
    files = {"file": ("reply.mp3", audio_bytes, mime_type)}
    data = {"messaging_product": "whatsapp"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, files=files, data=data)
            if resp.status_code >= 400:
                logger.error("Meta media upload failed: %s %s", resp.status_code, resp.text)
                return None
            return resp.json().get("id")
    except Exception as e:
        logger.error("Meta media upload error: %s", e, exc_info=True)
        return None


async def _send_meta_audio(phone: str, media_id: str, phone_number_id: Optional[str]) -> bool:
    token = _get_meta_access_token()
    if not token or not media_id:
        return False
    pnid = phone_number_id or settings.META_WA_PHONE_NUMBER_ID
    if not pnid:
        return False
    url = f"{_get_meta_base_url()}/{pnid}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": phone.replace("+", ""),
        "type": "audio",
        "audio": {"id": media_id},
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                logger.error("Meta audio send failed: %s %s", resp.status_code, resp.text)
                return False
        return True
    except Exception as e:
        logger.error("Meta audio send error: %s", e, exc_info=True)
        return False


@router.get("/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    mode = params.get("hub.mode") or params.get("hub_mode")
    token = params.get("hub.verify_token") or params.get("hub_verify_token")
    challenge = params.get("hub.challenge") or params.get("hub_challenge")
    if mode == "subscribe" and token and token == settings.META_WA_VERIFY_TOKEN:
        return PlainTextResponse(challenge or "")
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook")
async def inbound_webhook(request: Request):
    payload = await request.json()
    await _handle_webhook_payload(payload)
    return Response(content="ok", media_type="text/plain")


async def _handle_webhook_payload(payload: dict) -> None:
    try:
        entries = payload.get("entry", [])
        for entry in entries:
            for change in entry.get("changes", []):
                value = change.get("value", {}) or {}
                metadata = value.get("metadata", {}) or {}
                phone_number_id = metadata.get("phone_number_id") or settings.META_WA_PHONE_NUMBER_ID

                messages = value.get("messages", []) or []
                contacts = value.get("contacts", []) or []
                profile_name = None
                if contacts and isinstance(contacts[0], dict):
                    profile_name = (contacts[0].get("profile") or {}).get("name")

                for msg in messages:
                    msg_id = msg.get("id")
                    if not msg_id:
                        logger.warning("Meta inbound message without id ignored")
                        continue
                    if _seen_meta_message_recently(msg_id):
                        continue

                    from_id = msg.get("from") or ""
                    phone = _normalize_wa_phone(from_id)
                    msg_type = msg.get("type", "text")
                    text_body = ""
                    mime_type = None

                    if msg_type == "text":
                        text_body = (msg.get("text") or {}).get("body", "")
                    elif msg_type == "audio":
                        audio = msg.get("audio") or {}
                        media_id = audio.get("id")
                        audio_bytes, mime_type = await _download_meta_media(media_id)
                        text_body = await _transcribe_audio_bytes(audio_bytes or b"", mime_type)
                        if not text_body:
                            logger.warning("Audio transcription empty for %s", phone)

                    logger.info(
                        "Meta inbound | pnid=%s | from=%s | type=%s | text=%s",
                        phone_number_id, phone, msg_type, (text_body or "")[:120],
                    )
                    await _process_incoming_message(
                        phone=phone,
                        message_body=text_body,
                        msg_type=msg_type,
                        profile_name=profile_name,
                        phone_number_id=phone_number_id,
                    )
    except Exception as e:
        logger.error("Meta webhook processing error: %s", e, exc_info=True)


async def _process_incoming_message(
    phone: str,
    message_body: str,
    msg_type: str,
    profile_name: Optional[str],
    phone_number_id: str,
) -> None:
    db = get_db()

    # ── Blocked contact check ─────────────────────────────────────────────────
    blocked = await db.blocked_contacts.find_one({"phone": phone})
    if blocked:
        logger.info("🚫 Ignoring message from blocked contact: %s", phone)
        return

    # ── Handover check: if manager is handling, AI stays silent ──────────────
    conv_check = await db.conversations.find_one(
        {"phone": phone, "channel": "whatsapp"},
        sort=[("updated_at", -1)],
    )
    if conv_check and conv_check.get("handover_active"):
        logger.info("Handover active for %s — AI skipping response", phone)
        await db.conversations.update_one(
            {"session_id": conv_check["session_id"]},
            {
                "$inc": {"unread_count": 1},
                "$set": {
                    "last_message_preview": (message_body or "")[:80],
                    "updated_at": datetime.utcnow(),
                },
            },
        )
        return

    if msg_type != "text" and not message_body:
        await _send_meta_whatsapp(
            phone,
            "Sorry, I couldn't understand the voice note. Please type your message.",
            phone_number_id,
        )
        return

    if not (message_body or "").strip():
        logger.info("Ignoring empty WhatsApp text payload from %s", phone)
        return

    # ── Call request ──────────────────────────────────────────────────────────
    if msg_type == "text" and _is_call_request(message_body):
        profile = get_hotel_profile()
        room_id = f"wa_{phone.replace('+', '')}_{int(time.time())}"
        await _send_meta_whatsapp(phone, _build_call_options_message(profile, room_id), phone_number_id)
        return

    session = await _get_or_create_whatsapp_session(db, phone, profile_name)
    session_id = session["session_id"]

    # ── Detect label from message intent ──────────────────────────────────────
    body_lower = (message_body or "").lower()
    if any(k in body_lower for k in ["book", "reserv", "room", "suite", "check-in", "checkin"]):
        _inbox_label = "Booking"
    elif any(k in body_lower for k in ["cancel", "refund"]):
        _inbox_label = "Cancellation"
    elif any(k in body_lower for k in ["info", "price", "avail", "?", "how", "what"]):
        _inbox_label = "Info"
    else:
        _inbox_label = session.get("label")

    # ── Update inbox metadata ─────────────────────────────────────────────────
    _inbox_update: dict = {
        "$set": {
            "last_message_preview": (message_body or "")[:80],
            "guest_name": profile_name or session.get("context", {}).get("guest_name"),
            "updated_at": datetime.utcnow(),
        },
        "$inc": {"unread_count": 1},
    }
    if _inbox_label:
        _inbox_update["$set"]["label"] = _inbox_label
    await db.conversations.update_one({"session_id": session_id}, _inbox_update)

    # ── Broadcast real-time update to dashboard WebSocket clients ─────────────
    try:
        from routers.admin_extras import broadcast_new_message
        asyncio.create_task(broadcast_new_message(
            session_id=session_id,
            phone=phone,
            preview=(message_body or "")[:80],
            label=_inbox_label,
        ))
    except Exception:
        pass

    # ── Quick greetings → human-like response (new & returning guests) ──────────
    if msg_type == "text" and _is_quick_greeting(message_body):
        import random
        profile = get_hotel_profile()
        has_history = bool(session.get("messages"))
        if has_history:
            # Returning guest — vary the response so it doesn’t feel like a bot
            guest_name = profile_name or (session.get("context") or {}).get("guest_name", "")
            name_part = f" {guest_name}" if guest_name else ""
            options = [
                f"Hello{name_part}! \U0001f60a How can I help you today?",
                f"Good to hear from you{name_part}! How may I assist you?",
                f"Hi{name_part}! What can I help you with today?",
                f"Welcome back{name_part}! How may I assist you?",
                f"Hello{name_part}! What can I do for you today?",
            ]
            greeting = random.choice(options)
        else:
            # First-time guest — send full welcome with capabilities list
            greeting = GREETING_WHATSAPP.format(
                hotel_name=profile["name"],
                receptionist_name=profile["receptionist_name"],
            )
        sent = await _send_meta_whatsapp(phone, greeting, phone_number_id)
        if sent:
            await db.conversations.update_one(
                {"session_id": session_id},
                {
                    "$push": {
                        "messages": {
                            "$each": [
                                {"role": "user", "content": message_body, "timestamp": datetime.utcnow()},
                                {"role": "assistant", "content": greeting, "timestamp": datetime.utcnow()},
                            ]
                        }
                    },
                    "$set": {"updated_at": datetime.utcnow()},
                },
            )
        return

    # ── Off-topic check: reply instantly, skip agent & tool calls ─────────────
    # Saves ~400-700 tokens per off-topic message.
    if msg_type == "text" and _is_off_topic(message_body):
        import random
        off_reply = random.choice(_OFF_TOPIC_REPLIES)
        logger.info("Off-topic message from %s — returning canned reply", phone)
        await _send_meta_whatsapp(phone, off_reply, phone_number_id)
        await db.conversations.update_one(
            {"session_id": session_id},
            {
                "$set": {
                    "last_message_preview": (message_body or "")[:80],
                    "updated_at": datetime.utcnow(),
                },
                "$inc": {"unread_count": 1},
            },
        )
        return

    # ── Full AI agent processing (only for hotel-related messages) ────────────
    agent = get_whatsapp_agent()
    try:
        response_text, tool_calls = await agent.process_message(
            session_id=session_id,
            user_message=message_body,
            phone=phone,
        )

        for tc in tool_calls:
            if tc["name"] == "create_booking":
                result = json.loads(tc["result"])
                if result.get("success"):
                    await _send_booking_confirmation_meta(phone, result, phone_number_id)
                    return

        if msg_type == "audio":
            audio_bytes = await _synthesize_tts_mp3(response_text)
            if audio_bytes:
                media_id = await _upload_meta_media(audio_bytes, mime_type="audio/mpeg")
                if media_id:
                    await _send_meta_audio(phone, media_id, phone_number_id)
                    return

        await _send_meta_whatsapp(phone, response_text, phone_number_id)

    except Exception as e:
        logger.error("Meta WhatsApp agent processing error: %s", e, exc_info=True)
        await _send_meta_whatsapp(
            phone,
            "I'm sorry, I'm experiencing a technical issue. Please try again or contact us directly.",
            phone_number_id,
        )


async def _send_meta_whatsapp(phone: str, message: str, phone_number_id: Optional[str]) -> bool:
    """Send a WhatsApp text message via Meta Cloud API."""
    token = _get_meta_access_token()
    if not token:
        logger.warning("Meta WhatsApp send skipped: access token missing")
        return False

    pnid = phone_number_id or settings.META_WA_PHONE_NUMBER_ID
    if not pnid:
        logger.warning("Meta WhatsApp send skipped: phone_number_id missing")
        return False

    url = f"{_get_meta_base_url()}/{pnid}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    parts = _chunk_message(message, 1500)

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            for part in parts:
                payload = {
                    "messaging_product": "whatsapp",
                    "to": phone.replace("+", ""),
                    "type": "text",
                    "text": {"body": part},
                }
                resp = await client.post(url, headers=headers, json=payload)
                logger.info("Meta WA send → %s | status=%s", phone, resp.status_code)
                if resp.status_code >= 400:
                    logger.error("Meta WA send failed: %s %s", resp.status_code, resp.text)
                    return False
        return True
    except Exception as e:
        logger.error("Meta WA send error: %s", e, exc_info=True)
        return False


async def _send_booking_confirmation_meta(
    phone: str, booking: dict, phone_number_id: str
) -> bool:
    """Send rich booking confirmation via Meta WhatsApp."""
    children_count = booking.get("children", 0) or 0
    children_str = f" + {children_count} children" if children_count > 0 else ""
    profile = get_hotel_profile()

    msg = BOOKING_CONFIRMATION_WHATSAPP.format(
        booking_id=booking.get("booking_id", ""),
        guest_name=booking.get("guest_name", ""),
        room_type=booking.get("room_type", "").title(),
        room_number=booking.get("room_number", ""),
        check_in_date=booking.get("check_in_date", ""),
        check_out_date=booking.get("check_out_date", ""),
        adults=booking.get("adults", 1),
        children_str=children_str,
        total_amount=booking.get("total_amount", ""),
        currency=profile.get("currency", "BDT"),
        checkin_time=booking.get("check_in_time", profile.get("checkin_time", "14:00")),
        checkout_time=booking.get("check_out_time", profile.get("checkout_time", "12:00")),
        hotel_address=booking.get("hotel_address", profile.get("address", "")),
        calendar_google_url=booking.get("calendar_google_url", "Not available"),
        calendar_ics_url=booking.get("calendar_ics_url", "Not available"),
    )
    return await _send_meta_whatsapp(phone, msg, phone_number_id)
