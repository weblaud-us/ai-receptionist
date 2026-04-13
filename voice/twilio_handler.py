"""
Twilio Voice Call Handler — Complete VAD-based implementation
Proper Voice Activity Detection so conversation flows naturally.

Flow:
  Caller rings → /voice/incoming returns <Connect><Stream>
  → WebSocket /voice/stream established
  → Greeting sent via TTS
  → VAD loop: IDLE → SPEAKING → TRAILING → STT → AI → TTS → IDLE
  → Status callback cleans up + sends WhatsApp summary

Key fixes applied:
  1. No <Say> before <Connect> (was causing call drop after greeting)
  2. track="inbound_track" (was "both_tracks" → echo loop)
  3. Proper VAD with SPEAKING/TRAILING states (not just RMS threshold)
  4. ai_speaking flag — drops caller audio while AI TTS is playing
  5. Greeting sent via actual TTS through WebSocket (not Twilio Polly)
  6. Booking confirmed via spoken response, saved to MongoDB by tools.py
"""

import asyncio
import audioop
import base64
import io
import json
import logging
import time
import uuid
import wave
from datetime import datetime
from enum import Enum
from typing import Optional

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, HTTPException, Header, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field
from twilio.twiml.voice_response import VoiceResponse, Connect

from ai.agent import get_voice_agent, create_conversation_with_memory
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

# ── VAD constants (tuned for 8kHz mulaw, 20ms chunks from Twilio) ─────────
SPEECH_THRESHOLD   = 200    # RMS above this = caller speaking (16-bit PCM)
SILENCE_END_CHUNKS = 60     # 60 × 20ms = 1.2s silence → utterance complete
MIN_SPEECH_CHUNKS  = 15     # ignore clips < 15 × 20ms = 300ms
MAX_SPEECH_CHUNKS  = 1500   # 1500 × 20ms = 30s hard cap
IDLE_TIMEOUT_SEC   = 50     # hang up if caller silent > 50s after greeting


class VADState(Enum):
    IDLE     = "idle"
    SPEAKING = "speaking"
    TRAILING = "trailing"


class OutboundCallRequest(BaseModel):
    to_phone: str = Field(..., description="E.164 phone, e.g. +8801XXXXXXXXX")
    message: str = Field(default="")


async def _require_admin_key(x_admin_key: str = Header(default="", alias="X-Admin-Key")) -> bool:
    if not settings.API_KEY_REQUIRED:
        return True
    if x_admin_key != settings.ADMIN_SECRET_KEY:
        raise HTTPException(401, "Invalid admin key")
    return True


def _build_ws_url(request: Request) -> str:
    base = (settings.WEBHOOK_BASE_URL or "").strip().rstrip("/")
    if not base:
        host = request.headers.get("host", "")
        base = f"https://{host}" if host else ""
    if base.startswith("https://"):
        ws = "wss://" + base[8:]
    elif base.startswith("http://"):
        ws = "ws://" + base[7:]
    else:
        ws = f"wss://{base}"
    url = f"{ws}/voice/stream"
    logger.info("🌐 Twilio WS URL: %s", url)
    return url


# ─────────────────────────────────────────────────────────────────────────────
# Incoming call webhook
# ─────────────────────────────────────────────────────────────────────────────

def _twiml_connect(request: Request) -> Response:
    """Build the <Connect><Stream> TwiML response (shared by all code paths)."""
    response = VoiceResponse()
    connect  = Connect()
    connect.stream(url=_build_ws_url(request), track="inbound_track")
    response.append(connect)
    return Response(content=str(response), media_type="application/xml")


@router.post("/incoming")
async def incoming_call(request: Request):
    """
    IDEMPOTENT — Twilio retries on 5xx so this MUST never crash.

    Key design: reserve the active_sessions slot BEFORE any `await`.
    asyncio is single-threaded so check+set is atomic (no other task can
    run between them). This prevents ALL race conditions from concurrent retries.

    Flow:
      1. Parse form (await request.form)
      2. If call_sid already in active_sessions → return TwiML immediately (retry)
      3. Reserve slot in active_sessions synchronously (no await, so atomic)
      4. DB insert + conversation creation (awaits, but slot already reserved)
      5. Return TwiML
    """
    try:
        form     = await request.form()
        call_sid = form.get("CallSid", "")
        caller   = form.get("From", "unknown")
        logger.info("📞 Inbound call %s from %s", call_sid, caller)

        # ── Twilio retry guard ────────────────────────────────────────────────
        if call_sid in active_sessions:
            logger.warning("♻️ Twilio retry for callSid=%s — returning existing TwiML", call_sid)
            return _twiml_connect(request)

        # ── Reserve slot BEFORE any await (atomic in asyncio) ────────────────
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

        # ── DB operations (with awaits — slot already reserved, no race possible) ─
        db = get_db()
        try:
            call_doc = CallSession(
                call_sid=call_sid, phone=caller, session_id=session_id
            ).model_dump(by_alias=True)
            await db.call_sessions.insert_one(call_doc)
        except Exception as db_err:
            logger.warning("call_sessions insert warning for %s: %s — %s",
                           call_sid, type(db_err).__name__, db_err)
            try:
                existing = await db.call_sessions.find_one({"call_sid": call_sid},
                                                            {"session_id": 1})
                if existing and existing.get("session_id"):
                    session_id = existing["session_id"]
                    active_sessions[call_sid]["session_id"] = session_id
            except Exception:
                pass

        try:
            await create_conversation_with_memory(
                session_id=session_id,
                phone=caller,
                channel=ConversationChannel.VOICE,
                call_sid=call_sid,
            )
        except Exception as conv_err:
            logger.warning("create_conversation warning for %s: %s", call_sid, conv_err)

        logger.info("✅ Call session ready: callSid=%s session=%s", call_sid, session_id)
        return _twiml_connect(request)
    except Exception as incoming_err:
        logger.error("voice/incoming fallback due to error: %s", incoming_err, exc_info=True)
        return _twiml_connect(request)


@router.post("/status")
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
                {"$set": {"status": "completed", "duration_sec": float(duration),
                          "ended_at": datetime.utcnow(), "updated_at": datetime.utcnow()}}
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
    Full VAD-based audio pipeline:
    1. Wait for 'start' event → send greeting via TTS
    2. IDLE: wait for speech (RMS > threshold)
    3. SPEAKING: buffer caller audio
    4. TRAILING: silence after speech — count down SILENCE_END_CHUNKS
    5. When trailing done → send buffer to Whisper STT
    6. STT result → AI Agent → TTS → send mulaw audio back to caller
    7. While AI is speaking, drop all incoming audio (ai_speaking=True)
    """
    logger.info("🔌 WS connecting from %s", websocket.client)
    await websocket.accept()

    stt   = get_stt()
    tts   = get_tts()
    agent = get_voice_agent()

    call_sid: Optional[str] = None
    sd:       Optional[dict] = None

    # VAD state machine per call
    vad_state     = VADState.IDLE
    speech_buf    = bytearray()
    silence_count = 0
    speech_chunks = 0
    idle_since    = time.time()

    try:
        async for raw in websocket.iter_text():
            msg   = json.loads(raw)
            event = msg.get("event")

            # ── connected ─────────────────────────────────────────────────
            if event == "connected":
                logger.info("✅ Twilio WS connected")

            # ── start ─────────────────────────────────────────────────────
            elif event == "start":
                meta      = msg.get("start", {})
                call_sid  = meta.get("callSid")
                stream_sid = meta.get("streamSid")
                logger.info("🎙 Stream START callSid=%s streamSid=%s", call_sid, stream_sid)

                sd = active_sessions.get(call_sid)
                if sd:
                    sd["stream_sid"] = stream_sid
                    sd["websocket"]  = websocket
                    idle_since = time.time()
                    # Send greeting through TTS (async task)
                    asyncio.create_task(_send_greeting(websocket, stream_sid, sd, tts, agent))
                else:
                    logger.error("❌ No session for callSid=%s. Active: %s",
                                 call_sid, list(active_sessions.keys()))

            # ── media ─────────────────────────────────────────────────────
            elif event == "media" and sd:
                # Drop audio while AI is talking (prevents echo processing)
                if sd.get("ai_speaking") or sd.get("busy") or not sd.get("greeting_sent"):
                    continue

                payload    = msg.get("media", {}).get("payload", "")
                if not payload:
                    continue
                chunk_bytes = base64.b64decode(payload)
                rms         = _chunk_rms(chunk_bytes)

                # ── VAD state machine ──────────────────────────────────────
                if vad_state == VADState.IDLE:
                    if rms >= SPEECH_THRESHOLD:
                        vad_state     = VADState.SPEAKING
                        speech_buf    = bytearray(chunk_bytes)
                        speech_chunks = 1
                        silence_count = 0
                        idle_since    = time.time()
                        logger.debug("🗣 VAD SPEAKING (rms=%d)", rms)
                    else:
                        # idle timeout
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
                        # Hard cap — process what we have
                        logger.info("⚠️ Max speech length reached, processing")
                        vad_state     = VADState.IDLE
                        sd["busy"]    = True
                        asyncio.create_task(
                            _process_turn(websocket, bytes(speech_buf), sd, stt, tts, agent)
                        )
                        speech_buf    = bytearray()
                        speech_chunks = 0

                elif vad_state == VADState.TRAILING:
                    speech_buf.extend(chunk_bytes)

                    if rms >= SPEECH_THRESHOLD:
                        # Speech resumed — back to SPEAKING
                        vad_state     = VADState.SPEAKING
                        silence_count = 0
                        speech_chunks += 1
                    else:
                        silence_count += 1
                        if silence_count >= SILENCE_END_CHUNKS:
                            vad_state = VADState.IDLE
                            if speech_chunks >= MIN_SPEECH_CHUNKS:
                                logger.debug("✅ Utterance complete (%d chunks)", speech_chunks)
                                sd["busy"] = True
                                asyncio.create_task(
                                    _process_turn(websocket, bytes(speech_buf), sd, stt, tts, agent)
                                )
                            else:
                                logger.debug("🔇 Too short (%d chunks), discarding", speech_chunks)
                            speech_buf    = bytearray()
                            speech_chunks = 0
                            silence_count = 0

            # ── stop ──────────────────────────────────────────────────────
            elif event == "stop":
                logger.info("🛑 Stream stop: %s", call_sid)
                # Process any remaining buffered speech
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
# Audio helpers
# ─────────────────────────────────────────────────────────────────────────────

def _chunk_rms(mulaw_bytes: bytes) -> int:
    """Compute RMS of a mulaw audio chunk (converted to 16-bit PCM)."""
    if not mulaw_bytes:
        return 0
    try:
        pcm = audioop.ulaw2lin(mulaw_bytes, 2)
        return audioop.rms(pcm, 2)
    except Exception:
        return 0


def _mulaw_to_wav(mulaw_bytes: bytes) -> bytes:
    """Convert 8kHz mulaw bytes → WAV file bytes (for Whisper)."""
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


async def _mp3_to_mulaw(mp3_bytes: bytes) -> bytes:
    """Convert MP3 → 8kHz mulaw via ffmpeg (required by Twilio Media Streams)."""
    import os, tempfile
    if not mp3_bytes:
        return b""
    in_p = out_p = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(mp3_bytes)
            in_p = f.name
        out_p = in_p + ".ul"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", in_p,
            "-ar", "8000", "-ac", "1",
            "-acodec", "pcm_mulaw", "-f", "mulaw", out_p,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error("ffmpeg error: %s", (stderr or b"").decode()[:200])
            return b""
        if os.path.exists(out_p):
            with open(out_p, "rb") as f:
                return f.read()
        return b""
    except FileNotFoundError:
        logger.error("ffmpeg not found — install from https://ffmpeg.org/")
        return b""
    except Exception as e:
        logger.error("_mp3_to_mulaw: %r", e)
        return b""
    finally:
        for p in (in_p, out_p):
            try:
                if p and os.path.exists(p):
                    os.unlink(p)
            except Exception:
                pass


async def _send_audio(websocket: WebSocket, stream_sid: str, audio_bytes: bytes):
    """Send raw mulaw bytes to caller in 20ms chunks."""
    if not stream_sid or not audio_bytes:
        return
    CHUNK = 160   # 20ms @ 8kHz mulaw
    for i in range(0, len(audio_bytes), CHUNK):
        chunk = audio_bytes[i:i + CHUNK]
        msg   = {
            "event":     "media",
            "streamSid": stream_sid,
            "media":     {"payload": base64.b64encode(chunk).decode()},
        }
        try:
            await websocket.send_text(json.dumps(msg))
            await asyncio.sleep(0.008)   # slight pacing
        except Exception:
            break


def _is_mp3_bytes(audio_bytes: bytes) -> bool:
    """Detect MP3 payload so we only run ffmpeg when conversion is required."""
    if not audio_bytes or len(audio_bytes) < 3:
        return False
    return (
        audio_bytes.startswith(b"ID3")
        or audio_bytes.startswith(b"\xff\xfb")
        or audio_bytes.startswith(b"\xff\xf3")
        or audio_bytes.startswith(b"\xff\xf2")
    )


async def _send_greeting(
    websocket: WebSocket,
    stream_sid: str,
    sd: dict,
    tts,
    agent,
):
    """Synthesise and send the AI greeting after stream starts."""
    try:
        await asyncio.sleep(0.4)   # let stream stabilise
        profile = get_hotel_profile()
        text    = GREETING_VOICE.format(
            hotel_name=profile["name"],
            receptionist_name=profile["receptionist_name"],
        )
        logger.info("🗣 Sending greeting: %s", text[:80])
        sd["ai_speaking"] = True

        tts_audio = await tts.synthesize(text)
        mulaw     = await _mp3_to_mulaw(tts_audio) if _is_mp3_bytes(tts_audio) else tts_audio
        await _send_audio(websocket, stream_sid, mulaw)

        # Save greeting to conversation history
        db = get_db()
        await db.conversations.update_one(
            {"session_id": sd["session_id"]},
            {
                "$push": {"messages": {
                    "role": "assistant", "content": text,
                    "timestamp": datetime.utcnow(),
                }},
                "$set": {"updated_at": datetime.utcnow()},
            }
        )
        sd["greeting_sent"] = True
        logger.info("✅ Greeting delivered")
    except Exception as e:
        logger.error("Greeting error: %s", e, exc_info=True)
        sd["greeting_sent"] = True   # unblock even if greeting failed
    finally:
        sd["ai_speaking"] = False


async def _process_turn(
    websocket: WebSocket,
    speech_bytes: bytes,
    sd: dict,
    stt,
    tts,
    agent,
):
    """
    Full turn: mulaw bytes → Whisper STT → AI Agent → ElevenLabs TTS → mulaw → caller.
    Booking is automatically saved to MongoDB by the AI tools during agent.process_message().
    """
    try:
        # 1. Convert mulaw → WAV and transcribe
        wav      = _mulaw_to_wav(speech_bytes)
        user_txt = await stt.transcribe(wav, language="en")
        user_txt = (user_txt or "").strip()

        if not user_txt or len(user_txt) < 2:
            logger.debug("🔇 Empty STT result, skipping turn")
            return

        logger.info("👤 Caller: %s", user_txt)

        # 2. AI Agent processes message → response + tool_calls
        #    Booking tool (create_booking) saves to MongoDB automatically inside this call
        sd["ai_speaking"] = True
        response_text, tool_calls = await agent.process_message(
            session_id=sd["session_id"],
            user_message=user_txt,
            phone=sd["phone"],
        )
        logger.info("🤖 AI: %s", response_text[:120])

        # 3. TTS synthesis
        tts_audio = await tts.synthesize(response_text)
        mulaw     = await _mp3_to_mulaw(tts_audio) if _is_mp3_bytes(tts_audio) else tts_audio

        # 4. Send audio back to caller
        stream_sid = sd.get("stream_sid")
        if stream_sid and mulaw:
            await _send_audio(websocket, stream_sid, mulaw)

        # 5. Log any bookings created
        for tc in tool_calls:
            if tc["name"] == "create_booking":
                try:
                    import json as _json
                    result = _json.loads(tc["result"])
                    if result.get("success"):
                        logger.info("📅 Booking saved: %s for %s",
                                    result.get("booking_id"), sd.get("phone"))
                except Exception:
                    pass

    except Exception as e:
        logger.error("Turn processing error: %s", e, exc_info=True)
        try:
            err_audio = await tts.synthesize(
                "I'm sorry sir, I experienced a technical issue. Please repeat that."
            )
            err_mulaw = await _mp3_to_mulaw(err_audio) if _is_mp3_bytes(err_audio) else err_audio
            await _send_audio(websocket, sd.get("stream_sid", ""), err_mulaw)
        except Exception:
            pass
    finally:
        sd["ai_speaking"] = False
        sd["busy"]        = False


# ─────────────────────────────────────────────────────────────────────────────
# Outbound call
# ─────────────────────────────────────────────────────────────────────────────

async def make_outbound_call(to_phone: str, message: str, booking_id: Optional[str] = None) -> dict:
    try:
        from twilio.rest import Client as TwilioClient
        profile = get_hotel_profile()
        sid  = profile.get("twilio_account_sid") or settings.TWILIO_ACCOUNT_SID
        auth = profile.get("twilio_auth_token")  or settings.TWILIO_AUTH_TOKEN
        from_num = profile.get("twilio_voice_number") or settings.TWILIO_PHONE_NUMBER
        client = TwilioClient(sid, auth)
        twiml  = VoiceResponse()
        twiml.say(message, voice="Polly.Joanna", language="en-US")
        call = client.calls.create(
            twiml=str(twiml), to=to_phone, from_=from_num,
            status_callback=f"{settings.WEBHOOK_BASE_URL}/voice/status",
        )
        logger.info("📞 Outbound call to %s: %s", to_phone, call.sid)
        return {"success": True, "call_sid": call.sid}
    except Exception as e:
        logger.error("Outbound call failed: %s", e)
        return {"success": False, "error": str(e)}


class OutboundCallRequest(BaseModel):
    to_phone: str = Field(..., description="E.164 phone, e.g. +8801XXXXXXXXX")
    message:  str = Field(default="")


@router.post("/outbound")
async def outbound_call(req: OutboundCallRequest, _auth=Depends(_require_admin_key)):
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_PHONE_NUMBER:
        raise HTTPException(400, "Twilio settings missing")
    msg = req.message.strip() or (
        f"Hello, this is Aria from {get_hotel_profile()['name']}. "
        "Please let us know how we can assist you."
    )
    result = await make_outbound_call(to_phone=req.to_phone.strip(), message=msg)
    if not result.get("success"):
        raise HTTPException(500, result.get("error"))
    return result
