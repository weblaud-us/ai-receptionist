"""
Twilio Voice Call Handler — ElevenLabs-only, low-latency natural conversation

Problems fixed:
  1. LATE GREETING   → greeting audio pre-cached at startup; sleep 0.4s → 0.1s
  2. SILENCE ON QUERY→ filler audio sent immediately after STT while LLM runs in parallel
  3. NO ffmpeg       → ElevenLabs returns ulaw_8000 natively, zero conversion
  4. TOKEN COST      → voice history 20 → 8 msgs; max_tokens 300 → 200

VAD flow:
  Call → /voice/incoming → <Connect><Stream>
  → WebSocket /voice/stream
  → Pre-cached greeting plays instantly
  → IDLE → SPEAKING → TRAILING → STT → filler + LLM (parallel) → TTS → IDLE
"""

import asyncio
import audioop
import base64
import io
import json
import logging
import random
import time
import uuid
import wave
from datetime import datetime
from enum import Enum
from typing import Optional

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field
from twilio.twiml.voice_response import VoiceResponse, Connect

from ai.agent import get_voice_agent, create_conversation_with_memory
from core.integrations.twilio_auth import verify_twilio, verify_twilio_websocket
from core.log import call_id_var
from ai.prompts import GREETING_VOICE
from voice.stt_tts import get_stt, get_tts
from database.mongodb import get_db
from database.tenancy import get_hotel_profile
from database.models import CallSession, ConversationChannel
from whatsapp.handler import send_conversation_summary_whatsapp
from config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/voice", tags=["Voice Calls"])

active_sessions: dict = {}

# ── VAD constants (8kHz mulaw, 20ms chunks from Twilio) ───────────────────
SPEECH_THRESHOLD   = 200    # RMS above this = caller speaking
SILENCE_END_CHUNKS = 60     # 60 × 20ms = 1.2 s silence → utterance done
MIN_SPEECH_CHUNKS  = 15     # ignore clips < 300 ms
MAX_SPEECH_CHUNKS  = 1500   # 30 s hard cap
IDLE_TIMEOUT_SEC   = 50     # hang up after 50 s of silence

# ── Audio cache ────────────────────────────────────────────────────────────
_greeting_cache: Optional[bytes] = None   # mulaw, pre-synthesised at startup
_filler_cache:   list[bytes]     = []     # short "thinking" phrases
_cache_lock = asyncio.Lock()

THINKING_PHRASES = [
    "One moment, please.",
    "Let me check that for you.",
    "Just a moment.",
    "Sure, let me look that up.",
]


class VADState(Enum):
    IDLE     = "idle"
    SPEAKING = "speaking"
    TRAILING = "trailing"


class OutboundCallRequest(BaseModel):
    to_phone: str = Field(..., description="E.164 phone, e.g. +8801XXXXXXXXX")
    message:  str = Field(default="")


from utils.auth import require_admin as _require_admin_key  # JWT (dashboard) or X-Admin-Key


def _build_ws_url(request: Request) -> str:
    base = (settings.WEBHOOK_BASE_URL or "").strip().rstrip("/")
    if not base:
        host = request.headers.get("host", "")
        base = f"https://{host}" if host else ""
    ws = (
        "wss://" + base[8:]  if base.startswith("https://")
        else "ws://" + base[7:] if base.startswith("http://")
        else f"wss://{base}"
    )
    url = f"{ws}/voice/stream"
    logger.debug("Twilio WS URL: %s", url)
    return url


# ─────────────────────────────────────────────────────────────────────────────
# Audio cache — pre-warm at startup
# ─────────────────────────────────────────────────────────────────────────────

async def _ensure_audio_cache(tts) -> None:
    """
    Synthesise greeting + filler phrases once at startup.
    ElevenLabs returns ulaw_8000 natively — stored as-is, no conversion.
    """
    global _greeting_cache, _filler_cache

    if _greeting_cache is not None:
        return

    async with _cache_lock:
        if _greeting_cache is not None:
            return

        logger.info("🔥 Pre-warming audio cache…")

        # Greeting
        try:
            profile = get_hotel_profile()
            text    = GREETING_VOICE.format(
                hotel_name=profile["name"],
                receptionist_name=profile["receptionist_name"],
            )
            _greeting_cache = await tts.synthesize(text)
            logger.info("✅ Greeting cached (%d bytes mulaw)", len(_greeting_cache))
        except Exception as e:
            logger.warning("Greeting cache failed: %s", e)
            _greeting_cache = b""   # mark attempted

        # Fillers
        results = []
        for phrase in THINKING_PHRASES:
            try:
                audio = await tts.synthesize(phrase)
                if audio:
                    results.append(audio)
            except Exception as e:
                logger.warning("Filler cache failed '%s': %s", phrase, e)
        _filler_cache = results
        logger.info("✅ %d filler phrases cached", len(_filler_cache))


def _pick_filler() -> Optional[bytes]:
    return random.choice(_filler_cache) if _filler_cache else None


# ─────────────────────────────────────────────────────────────────────────────
# Audio helpers — mulaw only, no ffmpeg
# ─────────────────────────────────────────────────────────────────────────────

def _chunk_rms(mulaw_bytes: bytes) -> int:
    """RMS of a mulaw chunk (converted to 16-bit PCM for measurement)."""
    if not mulaw_bytes:
        return 0
    try:
        pcm = audioop.ulaw2lin(mulaw_bytes, 2)
        return audioop.rms(pcm, 2)
    except Exception:
        return 0


def _mulaw_to_wav(mulaw_bytes: bytes) -> bytes:
    """8kHz mulaw → WAV for Whisper STT."""
    if not mulaw_bytes:
        return b""
    pcm = audioop.ulaw2lin(mulaw_bytes, 2)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(pcm)
    return buf.getvalue()


async def _send_audio(websocket: WebSocket, stream_sid: str, audio_bytes: bytes) -> None:
    """Stream mulaw bytes to Twilio in 20 ms chunks."""
    if not stream_sid or not audio_bytes:
        return
    CHUNK = 160   # 20 ms @ 8 kHz mulaw
    for i in range(0, len(audio_bytes), CHUNK):
        msg = {
            "event":     "media",
            "streamSid": stream_sid,
            "media":     {"payload": base64.b64encode(audio_bytes[i:i + CHUNK]).decode()},
        }
        try:
            await websocket.send_text(json.dumps(msg))
            await asyncio.sleep(0.005)   # light pacing
        except Exception:
            break


# ─────────────────────────────────────────────────────────────────────────────
# Incoming call webhook
# ─────────────────────────────────────────────────────────────────────────────

def _twiml_connect(request: Request) -> Response:
    response = VoiceResponse()
    connect  = Connect()
    connect.stream(url=_build_ws_url(request), track="inbound_track")
    response.append(connect)
    return Response(content=str(response), media_type="application/xml")


@router.get("/incoming")
async def incoming_call_get_fallback():
    return Response(content="Voice webhook expects POST", media_type="text/plain")


@router.post("/incoming", dependencies=[Depends(verify_twilio)])
async def incoming_call(request: Request):
    """IDEMPOTENT — Twilio retries on 5xx. Slot reserved before any await."""
    try:
        form     = await request.form()
        call_sid = form.get("CallSid", "")
        caller   = form.get("From", "unknown")
        logger.info("📞 Inbound call %s from %s", call_sid, caller)

        if call_sid in active_sessions:
            logger.warning("♻️ Twilio retry callSid=%s", call_sid)
            return _twiml_connect(request)

        session_id = str(uuid.uuid4())
        active_sessions[call_sid] = {
            "session_id":    session_id,
            "phone":         caller,
            "stream_sid":    None,
            "websocket":     None,
            "busy":          False,
            "ai_speaking":   False,
            "greeting_sent": False,
            "last_speech_ts": time.time(),
        }

        db = get_db()
        try:
            call_doc = CallSession(
                call_sid=call_sid, phone=caller, session_id=session_id
            ).model_dump(by_alias=True)
            await db.call_sessions.insert_one(call_doc)
        except Exception as db_err:
            logger.warning("call_sessions insert warning %s: %s", call_sid, db_err)
            try:
                existing = await db.call_sessions.find_one(
                    {"call_sid": call_sid}, {"session_id": 1}
                )
                if existing and existing.get("session_id"):
                    session_id = existing["session_id"]
                    active_sessions[call_sid]["session_id"] = session_id
            except Exception:
                pass

        try:
            await create_conversation_with_memory(
                session_id=session_id, phone=caller,
                channel=ConversationChannel.VOICE, call_sid=call_sid,
            )
        except Exception as conv_err:
            logger.warning("create_conversation warning %s: %s", call_sid, conv_err)

        logger.info("✅ Session ready: callSid=%s session=%s", call_sid, session_id)
        return _twiml_connect(request)
    except Exception as e:
        logger.error("voice/incoming error: %s", e, exc_info=True)
        return _twiml_connect(request)


@router.post("/status", dependencies=[Depends(verify_twilio)])
async def call_status(request: Request):
    form     = await request.form()
    call_sid = form.get("CallSid")
    status   = form.get("CallStatus")
    duration = form.get("CallDuration", "0")
    logger.info("📞 Call %s → %s (%ss)", call_sid, status, duration)

    if status in ["completed", "failed", "busy", "no-answer"]:
        sd = active_sessions.pop(call_sid, None)
        if sd:
            db = get_db()
            await db.conversations.update_one(
                {"session_id": sd["session_id"]},
                {"$set": {
                    "status": "completed",
                    "duration_sec": float(duration),
                    "ended_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                }}
            )
            await db.call_sessions.delete_one({"call_sid": call_sid})
            if status == "completed" and sd.get("phone"):
                asyncio.create_task(send_conversation_summary_whatsapp(
                    session_id=sd["session_id"], phone=sd["phone"],
                    channel="call", hotel_id="default",
                ))
    return Response(content="", status_code=204)


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket — Twilio Media Stream
# ─────────────────────────────────────────────────────────────────────────────

@router.websocket("/stream")
async def media_stream(websocket: WebSocket):
    """
    VAD-based audio pipeline.
    Greeting → pre-cached mulaw, plays in ~100 ms.
    Filler   → plays immediately after STT while LLM runs in background.
    All audio is native mulaw — no ffmpeg, no conversion.
    """
    if not verify_twilio_websocket(websocket):
        await websocket.close(code=1008)  # before accept() → handshake rejected with 403
        return
    await websocket.accept()

    stt   = get_stt()
    tts   = get_tts()
    agent = get_voice_agent()

    # Ensure cache is warm (no-op if already done)
    asyncio.create_task(_ensure_audio_cache(tts))

    call_sid: Optional[str] = None
    sd:       Optional[dict] = None

    vad_state     = VADState.IDLE
    speech_buf    = bytearray()
    silence_count = 0
    speech_chunks = 0
    idle_since    = time.time()

    try:
        async for raw in websocket.iter_text():
            msg   = json.loads(raw)
            event = msg.get("event")

            if event == "connected":
                logger.info("✅ Twilio WS connected")

            elif event == "start":
                meta       = msg.get("start", {})
                call_sid   = meta.get("callSid")
                stream_sid = meta.get("streamSid")
                call_id_var.set(call_sid)
                logger.info("🎙 Stream START callSid=%s", call_sid)

                sd = active_sessions.get(call_sid)
                if sd:
                    sd["stream_sid"] = stream_sid
                    sd["websocket"]  = websocket
                    idle_since       = time.time()
                    asyncio.create_task(
                        _send_greeting(websocket, stream_sid, sd, tts, agent)
                    )
                else:
                    logger.error("❌ No session for callSid=%s", call_sid)

            elif event == "media" and sd:
                if sd.get("ai_speaking") or sd.get("busy") or not sd.get("greeting_sent"):
                    continue

                payload = msg.get("media", {}).get("payload", "")
                if not payload:
                    continue
                chunk_bytes = base64.b64decode(payload)
                rms         = _chunk_rms(chunk_bytes)

                if vad_state == VADState.IDLE:
                    if rms >= SPEECH_THRESHOLD:
                        vad_state     = VADState.SPEAKING
                        speech_buf    = bytearray(chunk_bytes)
                        speech_chunks = 1
                        silence_count = 0
                        idle_since    = time.time()
                    else:
                        if time.time() - idle_since > IDLE_TIMEOUT_SEC:
                            logger.info("⏰ Idle timeout — ending call %s", call_sid)
                            break

                elif vad_state == VADState.SPEAKING:
                    speech_buf.extend(chunk_bytes)
                    speech_chunks += 1
                    if rms < SPEECH_THRESHOLD:
                        vad_state     = VADState.TRAILING
                        silence_count = 1
                    elif speech_chunks >= MAX_SPEECH_CHUNKS:
                        logger.info("⚠️ Max speech cap — processing now")
                        vad_state  = VADState.IDLE
                        sd["busy"] = True
                        asyncio.create_task(
                            _process_turn(websocket, bytes(speech_buf), sd, stt, tts, agent)
                        )
                        speech_buf    = bytearray()
                        speech_chunks = 0

                elif vad_state == VADState.TRAILING:
                    speech_buf.extend(chunk_bytes)
                    if rms >= SPEECH_THRESHOLD:
                        vad_state     = VADState.SPEAKING
                        silence_count = 0
                        speech_chunks += 1
                    else:
                        silence_count += 1
                        if silence_count >= SILENCE_END_CHUNKS:
                            vad_state = VADState.IDLE
                            if speech_chunks >= MIN_SPEECH_CHUNKS:
                                sd["busy"] = True
                                asyncio.create_task(
                                    _process_turn(
                                        websocket, bytes(speech_buf), sd, stt, tts, agent
                                    )
                                )
                            speech_buf    = bytearray()
                            speech_chunks = 0
                            silence_count = 0

            elif event == "stop":
                logger.info("🛑 Stream stop: %s", call_sid)
                if sd and speech_buf and speech_chunks >= MIN_SPEECH_CHUNKS and not sd.get("busy"):
                    sd["busy"] = True
                    asyncio.create_task(
                        _process_turn(websocket, bytes(speech_buf), sd, stt, tts, agent)
                    )
                if sd:
                    await agent.end_conversation(sd["session_id"])
                break

    except WebSocketDisconnect:
        logger.info("WS disconnected (call_sid=%s)", call_sid)
    except Exception as e:
        logger.error("WS error: %s", e, exc_info=True)
    finally:
        if call_sid:
            active_sessions.pop(call_sid, None)


# ─────────────────────────────────────────────────────────────────────────────
# Greeting
# ─────────────────────────────────────────────────────────────────────────────

async def _send_greeting(
    websocket: WebSocket,
    stream_sid: str,
    sd: dict,
    tts,
    agent,
) -> None:
    """Play pre-cached greeting. Falls back to fresh synthesis on cache miss."""
    global _greeting_cache
    try:
        await asyncio.sleep(0.1)   # let stream stabilise
        sd["ai_speaking"] = True

        if _greeting_cache:
            mulaw = _greeting_cache
            logger.info("🗣 Greeting from cache (%d bytes)", len(mulaw))
        else:
            profile = get_hotel_profile()
            text    = GREETING_VOICE.format(
                hotel_name=profile["name"],
                receptionist_name=profile["receptionist_name"],
            )
            logger.info("🗣 Synthesising greeting (cache miss)")
            mulaw           = await tts.synthesize(text)
            _greeting_cache = mulaw

        await _send_audio(websocket, stream_sid, mulaw)

        # Persist greeting to conversation history
        profile = get_hotel_profile()
        greeting_text = GREETING_VOICE.format(
            hotel_name=profile["name"],
            receptionist_name=profile["receptionist_name"],
        )
        db = get_db()
        await db.conversations.update_one(
            {"session_id": sd["session_id"]},
            {
                "$push": {"messages": {
                    "role": "assistant", "content": greeting_text,
                    "timestamp": datetime.utcnow(),
                }},
                "$set": {"updated_at": datetime.utcnow()},
            }
        )
        sd["greeting_sent"] = True
        logger.info("✅ Greeting delivered")
    except Exception as e:
        logger.error("Greeting error: %s", e, exc_info=True)
        sd["greeting_sent"] = True   # unblock VAD even on failure
    finally:
        sd["ai_speaking"] = False


# ─────────────────────────────────────────────────────────────────────────────
# Turn processing — filler + LLM in parallel (silence fix)
# ─────────────────────────────────────────────────────────────────────────────

async def _process_turn(
    websocket: WebSocket,
    speech_bytes: bytes,
    sd: dict,
    stt,
    tts,
    agent,
) -> None:
    """
    mulaw → Whisper STT → [filler audio plays] + [LLM runs] → ElevenLabs TTS → caller

    Silence fix:
      After STT we immediately play a short filler ("One moment, please.")
      while the LLM call runs as a background task.
      Caller hears audio within ~200 ms of finishing their sentence.
    """
    try:
        # 1. STT
        wav      = _mulaw_to_wav(speech_bytes)
        user_txt = await stt.transcribe(wav, language="en")
        user_txt = (user_txt or "").strip()

        if not user_txt or len(user_txt) < 2:
            logger.debug("🔇 Empty STT, skipping turn")
            return

        logger.debug("👤 Caller: %s", user_txt)  # transcripts: DEBUG only

        # 2. Launch LLM in background
        sd["ai_speaking"] = True
        stream_sid = sd.get("stream_sid", "")

        llm_task = asyncio.create_task(
            agent.process_message(
                session_id=sd["session_id"],
                user_message=user_txt,
                phone=sd["phone"],
            )
        )

        # 3. Play filler immediately — caller never hears dead silence
        filler = _pick_filler()
        if filler and stream_sid:
            try:
                await _send_audio(websocket, stream_sid, filler)
            except Exception as fe:
                logger.debug("Filler send error (non-fatal): %s", fe)

        # 4. Wait for LLM
        response_text, tool_calls = await llm_task
        logger.debug("🤖 AI: %s", response_text[:120])

        # 5. TTS — ElevenLabs returns mulaw directly
        mulaw = await tts.synthesize(response_text)

        # 6. Send response
        if stream_sid and mulaw:
            await _send_audio(websocket, stream_sid, mulaw)

        # 7. Log bookings
        for tc in tool_calls:
            if tc["name"] == "create_booking":
                try:
                    import json as _j
                    r = _j.loads(tc["result"])
                    if r.get("success"):
                        logger.info("📅 Booking %s saved for %s",
                                    r.get("booking_id"), sd.get("phone"))
                except Exception:
                    pass

    except Exception as e:
        logger.error("Turn error: %s", e, exc_info=True)
        try:
            err = await tts.synthesize(
                "I am sorry, I had a brief issue. Could you please repeat that?"
            )
            await _send_audio(websocket, sd.get("stream_sid", ""), err)
        except Exception:
            pass
    finally:
        sd["ai_speaking"] = False
        sd["busy"]        = False


# ─────────────────────────────────────────────────────────────────────────────
# Outbound call
# ─────────────────────────────────────────────────────────────────────────────

async def make_outbound_call(
    to_phone: str, message: str, booking_id: Optional[str] = None
) -> dict:
    try:
        from twilio.rest import Client as TwilioClient
        profile  = get_hotel_profile()
        sid      = profile.get("twilio_account_sid") or settings.TWILIO_ACCOUNT_SID
        auth     = profile.get("twilio_auth_token")  or settings.TWILIO_AUTH_TOKEN
        from_num = profile.get("twilio_voice_number") or settings.TWILIO_PHONE_NUMBER
        client   = TwilioClient(sid, auth)
        twiml    = VoiceResponse()
        twiml.say(message, voice=settings.OUTBOUND_TTS_VOICE, language="en-US")
        call = client.calls.create(
            twiml=str(twiml), to=to_phone, from_=from_num,
            status_callback=f"{settings.WEBHOOK_BASE_URL}/voice/status",
        )
        logger.info("📞 Outbound call %s → %s", call.sid, to_phone)
        return {"success": True, "call_sid": call.sid}
    except Exception as e:
        logger.error("Outbound call failed: %s", e)
        return {"success": False, "error": str(e)}


@router.post("/outbound")
async def outbound_call(req: OutboundCallRequest, _auth=Depends(_require_admin_key)):
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_PHONE_NUMBER:
        raise HTTPException(400, "Twilio settings missing")
    profile = get_hotel_profile()
    # ⚖ AI disclosure: an automated voice must say it is one, even on a one-way message.
    disclosure = (
        f"Hello, this is {profile['receptionist_name']}, the virtual assistant for {profile['name']}. "
    )
    msg = disclosure + (req.message.strip() or "Please let us know how we can assist you.")
    result = await make_outbound_call(to_phone=req.to_phone.strip(), message=msg)
    if not result.get("success"):
        raise HTTPException(500, result.get("error"))
    return result
