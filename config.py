"""
Hotel AI Receptionist — Configuration
LLM: OpenAI (primary) | Gemini (alternative)
STT: OpenAI Whisper
TTS: ElevenLabs (primary) | OpenAI TTS (fallback)
"""

from enum import Enum
from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field


class LLMProvider(str, Enum):
    OPENAI = "openai"
    GEMINI = "gemini"


class STTProvider(str, Enum):
    OPENAI_WHISPER = "openai_whisper"


class TTSProvider(str, Enum):
    ELEVENLABS = "elevenlabs"
    OPENAI     = "openai"


class Settings(BaseSettings):
    # ── Application ───────────────────────────────────────
    APP_NAME:                str  = "Hotel AI Receptionist"
    APP_ENV:                 str  = Field(default="development")
    DEBUG:                   bool = Field(default=True)
    HOST:                    str  = "0.0.0.0"
    PORT:                    int  = 8002
    WEBHOOK_BASE_URL:        str  = Field(default="")
    WEBHOOK_TIMEOUT_SECONDS: int  = Field(default=30)

    # ── MongoDB ───────────────────────────────────────────
    MONGODB_URI:     str = Field(default="mongodb://localhost:27017")
    MONGODB_DB_NAME: str = Field(default="hotel_ai_dev")

    # ── LLM ───────────────────────────────────────────────
    LLM_PROVIDER: LLMProvider = Field(default=LLMProvider.OPENAI)

    # OpenAI Chat Completions (used by all agent turns)
    OPENAI_API_KEY:   str   = Field(default="")
    OPENAI_MODEL:     str   = Field(default="gpt-4o-mini")
    OPENAI_TEMPERATURE: float = Field(default=0.6)

    # OpenAI Realtime (LiveKit agent only — native audio, ~300ms latency)
    OPENAI_VOICE_MODEL:    str = Field(default="gpt-4o-realtime-preview")
    OPENAI_REALTIME_VOICE: str = Field(default="shimmer")

    # OpenAI TTS (fallback when ElevenLabs is unavailable)
    OPENAI_TTS_MODEL: str = Field(default="gpt-4o-mini-tts")
    OPENAI_TTS_VOICE: str = Field(default="shimmer")

    # Gemini (alternative LLM)
    GEMINI_API_KEY: str = Field(default="")
    GEMINI_MODEL:   str = Field(default="gemini-1.5-pro")

    # ── STT ───────────────────────────────────────────────
    STT_PROVIDER: STTProvider = Field(default=STTProvider.OPENAI_WHISPER)

    # ── TTS ───────────────────────────────────────────────
    TTS_PROVIDER:      TTSProvider = Field(default=TTSProvider.ELEVENLABS)
    ELEVENLABS_API_KEY: str        = Field(default="")
    ELEVENLABS_VOICE_ID: str       = Field(default="21m00Tcm4TlvDq8ikWAM")

    # ── Twilio Voice ──────────────────────────────────────
    TWILIO_ACCOUNT_SID:       str  = Field(default="")
    TWILIO_AUTH_TOKEN:        str  = Field(default="")
    TWILIO_PHONE_NUMBER:      str  = Field(default="")
    TWILIO_WHATSAPP_NUMBER:   str  = Field(default="")
    VERIFY_TWILIO_SIGNATURE:  bool = Field(default=False)
    WHATSAPP_MAX_PARTS:       int  = Field(default=1)

    # ── Meta WhatsApp Cloud API ───────────────────────────
    META_WA_VERIFY_TOKEN:   str = Field(default="")
    META_WA_ACCESS_TOKEN:   str = Field(default="")
    META_WA_PHONE_NUMBER_ID: str = Field(default="")
    META_WABA_ID:           str = Field(default="")
    META_GRAPH_API_VERSION: str = Field(default="v20.0")

    # ── LiveKit ───────────────────────────────────────────
    LIVEKIT_URL:        str = Field(default="")
    LIVEKIT_API_KEY:    str = Field(default="")
    LIVEKIT_API_SECRET: str = Field(default="")
    CALL_BASE_URL:      str = Field(default="")

    # ── Conversation Memory ───────────────────────────────
    CONVERSATION_MEMORY_DAYS:         int = Field(default=15)
    CONVERSATION_MEMORY_MAX_MESSAGES: int = Field(default=20)

    # ── Hotel Profile ─────────────────────────────────────
    HOTEL_NAME:          str = Field(default="Grand Azure Hotel")
    RECEPTIONIST_NAME:   str = Field(default="Aria")
    HOTEL_PHONE:         str = Field(default="+8801234567890")
    HOTEL_ADDRESS:       str = Field(default="123 Main Street, Dhaka, Bangladesh")
    HOTEL_CHECKIN_TIME:  str = Field(default="14:00")
    HOTEL_CHECKOUT_TIME: str = Field(default="12:00")
    HOTEL_CURRENCY:      str = Field(default="BDT")
    HOTEL_TIMEZONE:      str = Field(default="Asia/Dhaka")

    # ── Security ──────────────────────────────────────────
    SECRET_KEY:       str  = Field(default="change-in-production")
    WEBHOOK_SECRET:   str  = Field(default="")
    ADMIN_SECRET_KEY: str  = Field(default="change-in-production")
    API_KEY_REQUIRED: bool = Field(default=False)

    # ── Logging ───────────────────────────────────────────
    LOG_LEVEL:        str = Field(default="INFO")
    LOG_FILE:         str = Field(default="hotel_ai.log")
    LOG_MAX_SIZE_MB:  int = Field(default=50)
    LOG_BACKUP_COUNT: int = Field(default=5)

    # ── Feature Flags ─────────────────────────────────────
    ENABLE_VOICE_CALLS:              bool = Field(default=True)
    ENABLE_WHATSAPP:                 bool = Field(default=True)
    ENABLE_BOOKING_CONFIRMATION_SMS: bool = Field(default=True)
    ENABLE_CALL_RECORDING:           bool = Field(default=False)
    ENABLE_ONE_DAY_BOOKING_REMINDER: bool = Field(default=True)
    BOOKING_REMINDER_POLL_MINUTES:   int  = Field(default=30)

    class Config:
        env_file            = str(Path(__file__).resolve().parent / ".env")
        env_file_encoding   = "utf-8"
        extra               = "ignore"   # silently ignore extra .env keys


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
