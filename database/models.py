"""
Pydantic Models — MongoDB Schema Definitions
"""

from datetime import datetime, date
from typing import Optional, List, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field
import uuid


# ─────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────

class RoomType(str, Enum):
    STANDARD = "standard"
    DELUXE = "deluxe"
    SUITE = "suite"
    EXECUTIVE = "executive"
    PRESIDENTIAL = "presidential"


class BedType(str, Enum):
    SINGLE = "single"
    DOUBLE = "double"
    QUEEN = "queen"
    KING = "king"
    TWIN = "twin"


class BookingStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CHECKED_IN = "checked_in"
    CHECKED_OUT = "checked_out"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class ConversationChannel(str, Enum):
    VOICE = "voice"
    WHATSAPP = "whatsapp"


class ConversationStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


# ─────────────────────────────────────────────
# ROOM MODELS
# ─────────────────────────────────────────────

class RoomPricing(BaseModel):
    base_price_per_night: float          # BDT
    weekend_surcharge_pct: float = 0.0   # % extra on weekends
    peak_season_surcharge_pct: float = 0.0
    discount_weekly_pct: float = 0.0     # % off for 7+ nights
    discount_monthly_pct: float = 0.0    # % off for 30+ nights
    tax_pct: float = 15.0                # VAT/tax


class RoomAmenities(BaseModel):
    wifi: bool = True
    ac: bool = True
    tv: bool = True
    minibar: bool = False
    balcony: bool = False
    sea_view: bool = False
    jacuzzi: bool = False
    kitchen: bool = False
    workspace: bool = False
    breakfast_included: bool = False


class Room(BaseModel):
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    room_number: str                     # "101", "202A"
    room_type: RoomType
    bed_type: BedType
    floor: int
    capacity: int                        # max guests
    size_sqft: Optional[int] = None
    description: str = ""
    amenities: RoomAmenities = RoomAmenities()
    pricing: RoomPricing
    images: List[str] = []               # URLs
    is_active: bool = True
    is_maintenance: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True


# ─────────────────────────────────────────────
# GUEST MODELS
# ─────────────────────────────────────────────

class GuestProfile(BaseModel):
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    phone: str                           # E.164 format: +8801XXXXXXXXX
    name: Optional[str] = None
    email: Optional[str] = None
    nationality: Optional[str] = None
    id_number: Optional[str] = None      # Passport / NID
    preferences: Dict[str, Any] = {}     # room_type, floor, etc.
    total_stays: int = 0
    vip_tier: str = "standard"           # standard | silver | gold | platinum
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_interaction: Optional[datetime] = None

    class Config:
        populate_by_name = True


# ─────────────────────────────────────────────
# BOOKING MODELS
# ─────────────────────────────────────────────

class BookingGuest(BaseModel):
    name: str
    phone: str
    email: Optional[str] = None
    adults: int = 1
    children: int = 0


class PricingBreakdown(BaseModel):
    base_total: float
    taxes: float
    discounts: float
    final_total: float
    per_night_avg: float
    nights: int
    currency: str = "BDT"


class Booking(BaseModel):
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    booking_id: str = Field(default_factory=lambda: f"HTL{uuid.uuid4().hex[:8].upper()}")
    guest_phone: str
    guest_name: str
    guest_email: Optional[str] = None
    room_id: str
    room_number: str
    room_type: RoomType
    check_in_date: date
    check_out_date: date
    adults: int = 1
    children: int = 0
    special_requests: Optional[str] = None
    pricing: PricingBreakdown
    status: BookingStatus = BookingStatus.PENDING
    payment_status: str = "pending"      # pending | partial | paid
    source: str = "ai_receptionist"      # ai_receptionist | whatsapp | web | walk_in
    booking_notes: List[str] = []
    confirmation_sent: bool = False
    calendar_token: Optional[str] = None
    one_day_reminder_sent: bool = False
    one_day_reminder_sent_at: Optional[datetime] = None
    one_day_reminder_attempted_at: Optional[datetime] = None
    one_day_reminder_error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    checked_in_at: Optional[datetime] = None
    checked_out_at: Optional[datetime] = None

    class Config:
        populate_by_name = True


# ─────────────────────────────────────────────
# CONVERSATION MODELS
# ─────────────────────────────────────────────

class ConversationMessage(BaseModel):
    role: MessageRole
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    tool_calls: Optional[List[Dict]] = None
    tool_results: Optional[List[Dict]] = None
    audio_duration_sec: Optional[float] = None  # for voice messages


class ConversationContext(BaseModel):
    """Running context extracted during conversation"""
    guest_name: Optional[str] = None
    guest_phone: Optional[str] = None
    check_in_date: Optional[str] = None
    check_out_date: Optional[str] = None
    room_type: Optional[str] = None
    adults: int = 1
    children: int = 0
    special_requests: Optional[str] = None
    intent: Optional[str] = None          # booking | inquiry | cancellation | complaint
    booking_id: Optional[str] = None
    conversation_stage: str = "greeting"  # greeting | collecting_info | confirming | booked | support
    previous_bookings: List[Dict] = Field(default_factory=list)  # Guest's past bookings (injected from voice)


class Conversation(BaseModel):
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    phone: str
    channel: ConversationChannel
    call_sid: Optional[str] = None        # Twilio call SID (voice only)
    messages: List[ConversationMessage] = []
    context: ConversationContext = ConversationContext()
    status: ConversationStatus = ConversationStatus.ACTIVE
    booking_ids: List[str] = []           # bookings made in this conversation
    duration_sec: Optional[float] = None
    # ── Handover (AI ↔ Human Manager) ──────────────────────
    handover_active: bool = False          # True = manager is handling this chat
    handover_at: Optional[datetime] = None
    handover_note: Optional[str] = None
    guest_name: Optional[str] = None       # cached for inbox display
    last_message_preview: Optional[str] = None
    unread_count: int = 0
    label: Optional[str] = None           # "Booking", "Info", "Cancellation", etc.
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None

    class Config:
        populate_by_name = True


# ─────────────────────────────────────────────
# AGENT SETTINGS MODEL
# ─────────────────────────────────────────────

class BusinessHours(BaseModel):
    enabled: bool = True
    open: str = "08:00"
    close: str = "22:00"


class AgentSettings(BaseModel):
    id: str = Field(default="singleton", alias="_id")
    greeting_message: str = "Hello! Welcome to our hotel. I'm Aria, your AI concierge. How can I assist you today?"
    personality: str = "Professional & Formal"          # Professional & Formal | Friendly & Casual | Luxury & Premium
    response_language: str = "All languages (Auto-detect)"
    agent_active: bool = True
    auto_booking_confirmation: bool = True
    guest_reminders: bool = True
    notify_on_escalation: bool = False
    away_mode: bool = False
    escalation_email: str = ""
    business_hours: Dict[str, BusinessHours] = Field(default_factory=lambda: {
        "mon": BusinessHours(), "tue": BusinessHours(), "wed": BusinessHours(),
        "thu": BusinessHours(), "fri": BusinessHours(),
        "sat": BusinessHours(open="08:00", close="22:00"),
        "sun": BusinessHours(enabled=False, open="", close=""),
    })
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True


# ─────────────────────────────────────────────
# KNOWLEDGE BASE MODEL
# ─────────────────────────────────────────────

class KnowledgeItem(BaseModel):
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    title: str
    content: str
    tags: List[str] = []
    is_active: bool = True
    query_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True


# ─────────────────────────────────────────────
# CALL SESSION (real-time call state)
# ─────────────────────────────────────────────

class CallSession(BaseModel):
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    call_sid: str
    phone: str
    session_id: str                       # links to Conversation
    websocket_connected: bool = False
    audio_buffer: List[str] = []          # base64 audio chunks (temp)
    partial_transcript: str = ""
    is_speaking: bool = False
    silence_counter: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_activity: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True


# ─────────────────────────────────────────────
# API Request/Response Models
# ─────────────────────────────────────────────

class AvailabilityRequest(BaseModel):
    check_in_date: date
    check_out_date: date
    room_type: Optional[RoomType] = None
    adults: int = 1
    children: int = 0


class AvailabilityResponse(BaseModel):
    available_rooms: List[Dict]
    check_in_date: date
    check_out_date: date
    nights: int
    currency: str = "BDT"


class BookingCreateRequest(BaseModel):
    guest_name: str
    guest_phone: str
    guest_email: Optional[str] = None
    room_id: str
    check_in_date: date
    check_out_date: date
    adults: int = 1
    children: int = 0
    special_requests: Optional[str] = None


class BookingResponse(BaseModel):
    success: bool
    booking: Optional[Dict] = None
    message: str = ""
    error: Optional[str] = None
