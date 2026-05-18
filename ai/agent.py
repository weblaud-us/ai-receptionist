"""
Hotel AI Receptionist Agent
Multi-turn agentic loop: LLM → Tools → LLM → Response

Fixes applied:
- Removed _is_pricing_request shortcut (was bypassing LLM, breaking natural flow)
- Voice model always uses standard chat completions (gpt-4o), never realtime model
- Booking save: tool executor (tools.py) writes to MongoDB directly; agent tracks booking_id
- Response style enforced per channel (voice=short, WhatsApp=formatted)
- Caller phone number injected into context so AI can look up bookings without asking
"""

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple, Any

from ai.llm_provider import get_llm_provider
from ai.tools import HOTEL_TOOLS, get_tool_executor
from ai.prompts import get_system_prompt, VOICE_BREVITY_REMINDER, detect_gender_preference
from config import settings
from database.mongodb import get_db
from database.models import (
    Conversation, ConversationMessage, ConversationContext,
    MessageRole, ConversationChannel, ConversationStatus
)
from database.tenancy import get_hotel_profile

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 5


def _get_memory_cutoff() -> datetime:
    days = max(1, int(getattr(settings, "CONVERSATION_MEMORY_DAYS", 15)))
    return datetime.utcnow() - timedelta(days=days)


def _get_memory_limit() -> int:
    return max(5, int(getattr(settings, "CONVERSATION_MEMORY_MAX_MESSAGES", 20)))


async def create_conversation_with_memory(
    session_id: str,
    phone: str,
    channel: ConversationChannel,
    call_sid: Optional[str] = None,
    guest_name: Optional[str] = None,
    previous_bookings: Optional[List[Dict]] = None,
) -> Dict:
    db = get_db()

    if not guest_name:
        guest = await db.guests.find_one({"phone": phone})
        if guest and guest.get("name"):
            guest_name = guest.get("name")

    cutoff   = _get_memory_cutoff()
    prev_list = await db.conversations.find(
        {"phone": phone, "updated_at": {"$gte": cutoff}}
    ).sort("updated_at", -1).limit(1).to_list(1)

    prev_doc     = prev_list[0] if prev_list else None
    prev_messages = []
    prev_context  = {}

    if prev_doc:
        prev_context  = prev_doc.get("context", {}) or {}
        history       = prev_doc.get("messages", []) or []
        limit         = _get_memory_limit()
        prev_messages = history[-limit:]

    context = ConversationContext(
        guest_phone=phone,
        guest_name=guest_name or prev_context.get("guest_name"),
        check_in_date=prev_context.get("check_in_date"),
        check_out_date=prev_context.get("check_out_date"),
        room_type=prev_context.get("room_type"),
        adults=prev_context.get("adults", 1),
        children=prev_context.get("children", 0),
        special_requests=prev_context.get("special_requests"),
        intent=prev_context.get("intent"),
        booking_id=None,
        conversation_stage="greeting",
        previous_bookings=previous_bookings or prev_context.get("previous_bookings", []),
    )

    conv = Conversation(
        session_id=session_id, phone=phone, channel=channel,
        call_sid=call_sid, messages=prev_messages, context=context,
    )
    doc = conv.model_dump(by_alias=True)
    await db.conversations.insert_one(doc)
    return doc


class HotelAgent:
    """
    Agentic AI Hotel Receptionist.
    Maintains per-session conversation history in MongoDB.
    Runs tool-use loop automatically.
    Works for both voice calls and WhatsApp.
    """

    def __init__(self, channel: ConversationChannel):
        self.channel  = channel
        self._llm     = None
        self.executor = get_tool_executor()
        self.is_voice = (channel == ConversationChannel.VOICE)

    @property
    def llm(self):
        if self._llm is None:
            self._llm = get_llm_provider()
        return self._llm

    async def process_message(
        self,
        session_id: str,
        user_message: str,
        phone: str,
    ) -> Tuple[str, List[Dict]]:
        """
        Process one user turn.
        Returns (assistant_response, list_of_tool_calls_executed).
        Booking is saved to MongoDB INSIDE the tool executor during create_booking.
        """
        db = get_db()
        conv_doc = await db.conversations.find_one({"session_id": session_id})
        if not conv_doc:
            logger.warning("Session %s not found, creating new", session_id)
            conv_doc = await self._create_conversation(session_id, phone)

        # ── Detect gender from current message and persist to context ──────────
        detected_gender = detect_gender_preference(user_message)
        if detected_gender:
            current_gender = (conv_doc.get("context") or {}).get("guest_gender")
            if detected_gender != current_gender:
                await db.conversations.update_one(
                    {"session_id": session_id},
                    {"$set": {"context.guest_gender": detected_gender}}
                )
                conv_doc.setdefault("context", {})["guest_gender"] = detected_gender
                logger.info("Gender updated to '%s' for session %s", detected_gender, session_id)
        messages = self._build_messages(conv_doc, user_message)
        all_tool_calls: List[Dict] = []

        for iteration in range(MAX_TOOL_ITERATIONS):
            response = await self.llm.chat_completion(
                messages=messages,
                tools=HOTEL_TOOLS,
                tool_choice="auto",
                temperature=0.5,
                # Voice: very short answers (1-2 sentences).
                # WhatsApp: concise but formatted. 400 tokens saves ~40% vs 700.
                max_tokens=200 if self.is_voice else 400,
            )

            assistant_content = response["content"]
            tool_calls        = response.get("tool_calls")

            # Fallback: some local models embed tool calls as text
            if not tool_calls:
                fallback, cleaned = self._extract_fallback_tool_calls(assistant_content)
                if fallback:
                    tool_calls        = fallback
                    assistant_content = cleaned

            # No tool calls → final response
            if not tool_calls:
                break

            messages.append({
                "role":       "assistant",
                "content":    assistant_content,
                "tool_calls": tool_calls,
            })

            tool_results = []
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    args = {}

                logger.info("🔧 Tool call: %s(%s)", fn_name, str(args)[:120])
                result = await self.executor.execute(fn_name, args)
                logger.info("🔧 Tool result: %s", result[:200])

                tool_results.append({
                    "role":         "tool",
                    "tool_call_id": tc["id"],
                    "content":      result,
                })
                all_tool_calls.append({"name": fn_name, "args": args, "result": result})

            messages.extend(tool_results)

        # Enforce style
        assistant_content = self._enforce_style(assistant_content)

        # Save turn to MongoDB
        await self._save_turn(session_id, user_message, assistant_content, all_tool_calls, phone)

        # Update conversation context (booking_ids etc.)
        await self._update_context(session_id, all_tool_calls)

        return assistant_content, all_tool_calls

    def _build_messages(self, conv_doc: Dict, user_message: str) -> List[Dict]:
        hotel  = get_hotel_profile()
        context = conv_doc.get("context", {})
        contact_phone = (
            (hotel.get("twilio_voice_number") or "").strip()
            if self.channel.value == "whatsapp"
            else ""
        ) or hotel["phone"]
        system = get_system_prompt(
            channel=self.channel.value,
            hotel_name=hotel["name"],
            receptionist_name=hotel["receptionist_name"],
            hotel_address=hotel["address"],
            checkin_time=hotel["checkin_time"],
            checkout_time=hotel["checkout_time"],
            currency=hotel["currency"],
            hotel_phone=contact_phone,
            context=context,  # Pass context for booking-aware greeting
        )
        messages = [{"role": "system", "content": system}]

        # Inject caller phone so AI doesn't ask for it again
        caller_phone = (
            conv_doc.get("phone")
            or (conv_doc.get("context") or {}).get("guest_phone")
            or ""
        )
        if caller_phone:
            messages.append({
                "role": "system",
                "content": (
                    f"CALLER PHONE: {caller_phone}. "
                    "When the guest asks about their booking, call get_booking_details "
                    f"with guest_phone='{caller_phone}' immediately."
                ),
            })

        # Voice: last 8 messages. WhatsApp: last 8 messages too — trimmed from
        # 12 to reduce input tokens while keeping enough booking context.
        history_limit = 8 if self.is_voice else 8
        history = conv_doc.get("messages", [])[-history_limit:]
        for m in history:
            role, content = m.get("role", "user"), m.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

        # Periodic voice brevity reminder
        if self.is_voice and len(history) % 4 == 0:
            messages.append({"role": "system", "content": VOICE_BREVITY_REMINDER})

        messages.append({"role": "user", "content": user_message})
        return messages

    def _enforce_style(self, text: str) -> str:
        """Remove AI mentions. Do NOT force-prepend 'Sir' — the LLM handles salutation."""
        if not text:
            return "How may I assist you today?"
        cleaned = re.sub(r"\bA\.I\.\b|\bAI\b|\bartificial intelligence\b", "", text, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        return cleaned

    def _extract_fallback_tool_calls(self, content: str) -> Tuple[Optional[List[Dict]], str]:
        """Handle tool calls embedded as text (some models)."""
        if not content:
            return None, content
        pattern = re.compile(
            r"<\|tool_call_start\|>\s*(?:\[(?P<a>.*?)\]|(?P<b>.*?))\s*<\|tool_call_end\|>",
            re.DOTALL,
        )
        match = pattern.search(content)
        if not match:
            return None, content

        call_text = (match.group("a") or match.group("b") or "").strip()
        cleaned   = re.sub(r"\s{2,}", " ", pattern.sub("", content)).strip()
        if not call_text:
            return None, cleaned

        fn_match = re.match(r"([a-zA-Z0-9_]+)\((.*)\)", call_text, re.DOTALL)
        if not fn_match:
            return None, cleaned

        fn_name = fn_match.group(1)
        args    = self._parse_kv_args(fn_match.group(2).strip())
        return [{
            "id":       f"fallback_{fn_name}",
            "function": {"name": fn_name, "arguments": json.dumps(args)},
            "type":     "function",
        }], cleaned

    def _parse_kv_args(self, args_str: str) -> Dict:
        if not args_str:
            return {}
        args: Dict[str, Any] = {}
        current = ""
        in_q, q_char = False, ""
        parts = []
        for ch in args_str:
            if ch in ('"', "'"):
                if in_q and ch == q_char:
                    in_q = False
                elif not in_q:
                    in_q, q_char = True, ch
            if ch == "," and not in_q:
                parts.append(current)
                current = ""
                continue
            current += ch
        if current:
            parts.append(current)
        for part in parts:
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            k = k.strip()
            v = v.strip().strip("\"' ")
            if v.lower() in ("true", "false"):
                args[k] = v.lower() == "true"
            else:
                try:
                    args[k] = float(v) if "." in v else int(v)
                except ValueError:
                    args[k] = v
        return args

    async def _create_conversation(self, session_id: str, phone: str) -> Dict:
        return await create_conversation_with_memory(
            session_id=session_id, phone=phone, channel=self.channel,
        )

    async def _save_turn(
        self, session_id: str, user_message: str,
        assistant_message: str, tool_calls: List[Dict], phone: str,
    ):
        db  = get_db()
        now = datetime.utcnow()
        user_msg = ConversationMessage(role=MessageRole.USER, content=user_message, timestamp=now)
        ai_msg   = ConversationMessage(
            role=MessageRole.ASSISTANT, content=assistant_message,
            timestamp=now, tool_calls=tool_calls or None,
        )
        await db.conversations.update_one(
            {"session_id": session_id},
            {
                "$push": {"messages": {"$each": [user_msg.model_dump(), ai_msg.model_dump()]}},
                "$set":  {"updated_at": now},
            }
        )
        if phone:
            await db.guests.update_one(
                {"phone": phone},
                {
                    "$set":         {"last_interaction": now, "updated_at": now},
                    "$setOnInsert": {"created_at": now, "vip_tier": "standard"},
                },
                upsert=True,
            )

    async def _update_context(self, session_id: str, tool_calls: List[Dict]):
        """Track booking IDs and conversation stage in MongoDB."""
        db = get_db()
        ctx_updates = {}
        for tc in tool_calls:
            if tc["name"] == "create_booking":
                try:
                    r = json.loads(tc["result"])
                    if r.get("success"):
                        ctx_updates["context.booking_id"]          = r.get("booking_id")
                        ctx_updates["context.conversation_stage"]  = "booked"
                        await db.conversations.update_one(
                            {"session_id": session_id},
                            {"$addToSet": {"booking_ids": r["booking_id"]}}
                        )
                        logger.info("✅ Booking ID %s added to conversation %s",
                                    r.get("booking_id"), session_id)
                except Exception as e:
                    logger.error("_update_context create_booking error: %s", e)

            elif tc["name"] == "check_room_availability":
                try:
                    a = tc["args"]
                    ctx_updates["context.check_in_date"]        = a.get("check_in_date")
                    ctx_updates["context.check_out_date"]       = a.get("check_out_date")
                    ctx_updates["context.conversation_stage"]   = "collecting_info"
                except Exception:
                    pass

        if ctx_updates:
            await db.conversations.update_one(
                {"session_id": session_id}, {"$set": ctx_updates}
            )

    async def end_conversation(self, session_id: str):
        db = get_db()
        await db.conversations.update_one(
            {"session_id": session_id},
            {"$set": {"status": ConversationStatus.COMPLETED,
                       "ended_at": datetime.utcnow(), "updated_at": datetime.utcnow()}}
        )


def get_voice_agent()    -> HotelAgent:
    return HotelAgent(channel=ConversationChannel.VOICE)

def get_whatsapp_agent() -> HotelAgent:
    return HotelAgent(channel=ConversationChannel.WHATSAPP)
