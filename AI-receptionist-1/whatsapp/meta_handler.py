"""
Meta WhatsApp Cloud API webhook handler.
Receives inbound messages and sends AI replies via Graph API.

Token-cost optimisations applied:
- Off-topic (non-hotel) messages get a lightweight canned reply — no agent call.
- Quick greetings bypass agent and return a static welcome.
- History trimmed to 8 messages (down from 12) via agent.py.
- max_tokens reduced to 400 for WhatsApp in agent.py.
"""

import json
import logging
import os
import tempfile
import uuid
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

from ai.agent import get_whatsapp_agent
from ai.prompts import GREETING_WHATSAPP, BOOKING_CONFIRMATION_WHATSAPP
from config import settings
from core import dedupe
from core.log import set_tenant
from core.integrations.meta_auth import verify_meta
from core.signed_links import build_call_link
from database.mongodb import get_db
from core.tenancy import resolve_by_whatsapp_phone_number_id
from core.tenancy.models import TenantConfig, legacy_hotel_tenant
from database.tenancy import tenant_profile
from core.repositories import Repos
from core.repositories.scoped import scoped_db
from core.services import whatsapp_outbox as wa_outbox
from whatsapp.handler import _get_or_create_whatsapp_session
from voice.stt_tts import get_stt, get_tts

logger = logging.getLogger(__name__)
router = APIRouter(tags=["WhatsApp Meta"])

async def _seen_meta_message_recently(message_id: Optional[str]) -> bool:
    """Meta retries webhooks; each message id is handled once across all API replicas (24 h)."""
    if not message_id:
        return False
    return not await dedupe.first_time("wa", message_id)


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
    # one-word / short greetings
    "hi", "hello", "hey", "start", "menu", "helo", "hii", "hiii",
    "greetings", "salaam", "salam", "howdy", "sup", "yo",
    # time-of-day greetings
    "good morning", "good afternoon", "good evening", "good night",
    # casual "how are you" variants — these are pleasantries, NOT off-topic
    "how are you", "how are you?", "how r u", "how r u?",
    "how are u", "how are u?", "hows it going", "how's it going",
    "how's it going?", "how are you doing", "how are you doing?",
    "how do you do", "how do you do?",
    # casual "what's up" variants
    "what's up", "what's up?", "whats up", "whats up?",
    "wassup", "wazzup", "sup?",
    # general openers
    "ok", "okay", "k", "fine", "yes", "no",
    "i am here", "i'm here", "present",
}

# Phrases that are casual conversation — handled naturally, never off-topic
_CASUAL_CONVERSATION = {
    "how are you", "how are you?", "how r u", "how r u?",
    "how are u", "how are u?", "hows it going", "how's it going",
    "how's it going?", "how are you doing", "how are you doing?",
    "how do you do", "how do you do?",
    "what's up", "what's up?", "whats up", "whats up?",
    "wassup", "wazzup", "sup", "sup?",
}

_CASUAL_REPLIES = [
    "I'm doing well, thank you for asking! 😊 How can I help you today?",
    "All good here, thanks! How may I assist you today?",
    "I'm great, thanks for asking! What can I do for you today?",
    "Doing well! How can I help you with your stay today?",
]


def _is_quick_greeting(text: str) -> bool:
    norm = (text or "").strip().lower().rstrip("!?.")
    return norm in _QUICK_GREETING_WORDS or norm.rstrip("?!.") in _QUICK_GREETING_WORDS


def _is_casual_conversation(text: str) -> bool:
    """Casual pleasantries like 'how are you', 'what's up' — respond warmly, not with hotel refusal."""
    norm = (text or "").strip().lower().rstrip("!?.")
    return norm in _CASUAL_CONVERSATION


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
    "That's a bit outside what I can help with, but I'm happy to assist with anything about your stay — room bookings, availability, pricing, or reservations. What can I do for you?",
    "I'm best at helping with hotel-related questions like bookings, room availability, and pricing. Is there anything about your stay I can assist with?",
    "Happy to help with anything hotel-related! Feel free to ask about room bookings, availability, or pricing. 😊",
]

_NAME_QUESTION_KEYWORDS = [
    "your name", "what is your name", "what's your name", "who are you", "who is this",
    "receptionist name", "name of receptionist", "front desk name", "your full name",
    "apnar nam", "apnar naam", "tomar nam", "tomar naam", "nam ki",
    "apnar nam ki", "tomar nam ki",
    "আপনার নাম", "আপনার নাম কী", "তোমার নাম", "নাম কী",
    "রিসেপশনিস্টের নাম", "রিসেপশনের নাম",
]


def _is_name_question(text: str) -> bool:
    norm = (text or "").strip().lower()
    if not norm:
        return False
    return any(k in norm for k in _NAME_QUESTION_KEYWORDS)


def _is_off_topic(text: str) -> bool:
    """
    Return True only when the message has NO hotel-related keywords AND
    is NOT a greeting or casual pleasantry.
    Greetings/pleasantries like 'how are you', 'what's up' are NEVER off-topic.
    """
    if not text or len(text.strip()) < 4:
        return False
    if _is_quick_greeting(text):
        return False
    if _is_casual_conversation(text):
        return False
    norm = text.lower()
    return not any(kw in norm for kw in _HOTEL_KEYWORDS)


def _build_call_options_message(profile: dict, room_id: str, tenant_id: Optional[str] = None) -> str:
    """Build message offering both Twilio PSTN and LiveKit browser call options."""
    twilio_number = (profile.get("twilio_voice_number") or settings.TWILIO_PHONE_NUMBER or "").strip()
    base = _get_call_base_url()

    lines = [
        f"You can speak with our receptionist *{profile.get('receptionist_name', 'Aria')}* "
        f"from *{profile.get('name', 'us')}* in two ways:\n"
    ]
    if twilio_number:
        lines.append(f"\ud83d\udcde *Real Phone Call:*\nCall us directly at *{twilio_number}*")
    if base:
        call_link = build_call_link(base, room_id, tenant_id)  # signed (incl. tenant), expires in 15 minutes
        lines.append(f"\ud83c\udf10 *Browser Call (no phone needed):*\nTap this link (valid for 15 minutes):\n{call_link}")

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
    """MP3 bytes for a WhatsApp voice reply (uploaded as audio/mpeg). Never the phone-line μ-law."""
    if not text:
        return None
    try:
        tts = get_tts()
        mp3 = getattr(tts, "synthesize_mp3", None)
        if mp3 is None:
            raise RuntimeError(f"{type(tts).__name__} can't produce MP3")
        audio = await mp3(text)
        if audio:
            return audio
        raise RuntimeError("empty MP3 from the TTS provider")
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
async def inbound_webhook(background: BackgroundTasks, body: bytes = Depends(verify_meta)):
    """
    Acknowledge immediately, process after the response. Meta retries webhooks that don't get
    a fast 200, which used to cause duplicate replies. With Redis the payload goes onto the job
    queue (the jobs worker replies, so an API restart can't lose it); otherwise it is processed
    in this process after the response.
    """
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid json")
    from jobs.queue import enqueue

    if not await enqueue("whatsapp_inbound", payload):
        background.add_task(_handle_webhook_payload, payload)
    return Response(content="ok", media_type="text/plain")


async def _handle_webhook_payload(payload: dict) -> None:
    try:
        entries = payload.get("entry", [])
        for entry in entries:
            for change in entry.get("changes", []):
                value = change.get("value", {}) or {}
                metadata = value.get("metadata", {}) or {}
                phone_number_id = metadata.get("phone_number_id") or settings.META_WA_PHONE_NUMBER_ID
                # Phase 2: which business owns the number that received this message?
                tenant = await resolve_by_whatsapp_phone_number_id(metadata.get("phone_number_id"))
                if tenant is None or not tenant.active:
                    logger.warning("WhatsApp message for an unassigned number id; ignored")
                    continue
                set_tenant(tenant.id)
                repos = None if tenant.is_legacy else Repos(get_db(), tenant.id)

                # Delivery receipts for messages staff sent from the dashboard (failed ones carry the reason).
                if repos is not None and value.get("statuses"):
                    await wa_outbox.apply_statuses(repos, value.get("statuses") or [])

                messages = value.get("messages", []) or []
                contacts = value.get("contacts", []) or []
                profile_name = None
                if contacts and isinstance(contacts[0], dict):
                    profile_name = (contacts[0].get("profile") or {}).get("name")
                if repos is not None:
                    for msg in messages:      # each inbound message reopens the 24-hour free-text window
                        await wa_outbox.note_inbound(repos, _normalize_wa_phone(msg.get("from") or ""),
                                                     name=profile_name)

                for msg in messages:
                    msg_id = msg.get("id")
                    if not msg_id:
                        logger.warning("Meta inbound message without id ignored")
                        continue
                    if await _seen_meta_message_recently(msg_id):
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

                    logger.info("Meta inbound | from=%s | type=%s | chars=%s",
                                phone, msg_type, len(text_body or ""))
                    logger.debug("Meta inbound text: %s", (text_body or "")[:120])  # DEBUG only
                    await _process_incoming_message(
                        phone=phone,
                        message_body=text_body,
                        msg_type=msg_type,
                        profile_name=profile_name,
                        phone_number_id=phone_number_id,
                        tenant=tenant,
                    )
    except Exception as e:
        logger.error("Meta webhook processing error: %s", e, exc_info=True)


async def _process_incoming_message(
    phone: str,
    message_body: str,
    msg_type: str,
    profile_name: Optional[str],
    phone_number_id: str,
    tenant: Optional[TenantConfig] = None,
) -> None:
    db = get_db()
    tenant = tenant or legacy_hotel_tenant()
    profile = tenant_profile(tenant)
    is_hotel = tenant.vertical == "hotel"

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

    # ── Opt-out / opt-in (Phase 6: revocation honoured on every channel) ─────
    if msg_type == "text":
        from core.compliance import optout

        kind = optout.classify(message_body, chat=True)
        if kind in ("stop", "start"):
            scope = None if tenant.is_legacy else tenant.id
            if kind == "stop":
                await optout.record_opt_out(db, phone, channel="whatsapp", source="whatsapp", tenant_id=scope)
                reply = (f"Done — {tenant.name} won't send you any more messages here. "
                         "Reply START if you change your mind.")
            else:
                await optout.record_opt_in(db, phone, channel="whatsapp", tenant_id=scope)
                reply = f"You're subscribed to messages from {tenant.name} again."
            await _send_meta_whatsapp(phone, reply, phone_number_id)
            return

    # ── Call request ──────────────────────────────────────────────────────────
    if msg_type == "text" and _is_call_request(message_body):
        room_id = f"wa_{uuid.uuid4().hex[:16]}"  # no phone number in URLs
        link_tenant = None if tenant.is_legacy else tenant.id
        await _send_meta_whatsapp(phone, _build_call_options_message(profile, room_id, link_tenant), phone_number_id)
        return

    session = await _get_or_create_whatsapp_session(db, phone, profile_name, tenant)
    session_id = session["session_id"]

    # ── Quick greetings & casual pleasantries → human-like warm response ─────
    if msg_type == "text" and _is_quick_greeting(message_body):
        import random
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
            ) if is_hotel else (
                f"Hello! Welcome to {profile['name']}. 😊\n"
                f"This is {profile['receptionist_name']}, {profile['name']}'s virtual assistant.\n"
                "How may I help you today?"
            )
        sent = await _send_meta_whatsapp(phone, greeting, phone_number_id)
        if sent:
            await scoped_db(tenant).conversations.update_one(
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
    # The keyword filter is tuned for hotels; other businesses always go to the agent.
    if is_hotel and msg_type == "text" and _is_off_topic(message_body):
        import random
        if _is_name_question(message_body):
            receptionist_name = (profile.get("receptionist_name") or settings.RECEPTIONIST_NAME or "Aria").strip()
            hotel_name = (profile.get("name") or settings.HOTEL_NAME or "the hotel").strip()
            # ⚖ honest about being an AI assistant
            off_reply = (
                f"I'm {receptionist_name}, the virtual assistant for {hotel_name}. "
                "I can help with bookings and questions about your stay, or pass a message to our team. "
                "How may I help you today?"
            )
        else:
            off_reply = random.choice(_OFF_TOPIC_REPLIES)
        logger.info("Off-topic message from %s — returning canned reply", phone)
        await _send_meta_whatsapp(phone, off_reply, phone_number_id)
        await scoped_db(tenant).conversations.update_one(
            {"session_id": session_id},
            {"$set": {"updated_at": datetime.utcnow()}},
        )
        return

    # ── Full AI agent processing (only for hotel-related messages) ────────────
    agent = get_whatsapp_agent()
    try:
        response_text, tool_calls = await agent.process_message(
            session_id=session_id,
            user_message=message_body,
            phone=phone,
            tenant=tenant,
        )

        for tc in tool_calls:
            if tc["name"] == "create_booking":
                result = json.loads(tc["result"])
                if result.get("success"):
                    await _send_booking_confirmation_meta(phone, result, phone_number_id, profile)
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
    """Send a WhatsApp text message via Meta Cloud API (one sender: core.integrations.whatsapp)."""
    from core.integrations.whatsapp import send_text

    return await send_text(phone, message, phone_number_id)


async def _send_booking_confirmation_meta(
    phone: str, booking: dict, phone_number_id: str, profile: Optional[dict] = None
) -> bool:
    """Send rich booking confirmation via Meta WhatsApp."""
    children_count = booking.get("children", 0) or 0
    children_str = f" + {children_count} children" if children_count > 0 else ""
    profile = profile or tenant_profile(legacy_hotel_tenant())

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
