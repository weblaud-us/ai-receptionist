"""
AI Receptionist — configuration.

Every variable the code reads is declared here and documented in .env.example.
Defaults are production-safe: a missing variable must never make the system less secure.
In production the app refuses to boot with unsafe settings (see `_production_guard`).
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_SECRETS = {"", "change-in-production"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Application ───────────────────────────────────────
    APP_ENV:          Literal["development", "staging", "production"] = Field(default="production")
    DEBUG:            bool = Field(default=False)
    HOST:             str  = "0.0.0.0"
    PORT:             int  = 8002
    WEBHOOK_BASE_URL: str  = Field(default="")   # public https URL Twilio/Meta call (signature checks use it)
    GIT_SHA:          str  = Field(default="")   # set by the deploy; tags Sentry releases

    # ── MongoDB ───────────────────────────────────────────
    MONGODB_URI:     str = Field(default="mongodb://localhost:27017")
    MONGODB_DB_NAME: str = Field(default="hotel_ai_dev")

    # ── OpenAI ────────────────────────────────────────────
    OPENAI_API_KEY:     str   = Field(default="")
    OPENAI_MODEL:       str   = Field(default="gpt-4o-mini")
    OPENAI_TEMPERATURE: float = Field(default=0.6)

    # OpenAI Realtime (LiveKit agent only)
    OPENAI_VOICE_MODEL:    str = Field(default="gpt-4o-realtime-preview")
    OPENAI_REALTIME_VOICE: str = Field(default="shimmer")

    # OpenAI TTS (fallback when ElevenLabs is unavailable)
    OPENAI_TTS_MODEL: str = Field(default="gpt-4o-mini-tts")
    OPENAI_TTS_VOICE: str = Field(default="shimmer")

    # ── STT / TTS ─────────────────────────────────────────
    STT_PROVIDER:        str = Field(default="openai_whisper")
    TTS_PROVIDER:        str = Field(default="elevenlabs")
    ELEVENLABS_API_KEY:  str = Field(default="")
    ELEVENLABS_VOICE_ID: str = Field(default="21m00Tcm4TlvDq8ikWAM")
    DEEPGRAM_API_KEY:    str = Field(default="")   # Phase 1 streaming STT

    # ── Twilio Voice ──────────────────────────────────────
    TWILIO_ACCOUNT_SID:         str  = Field(default="")
    TWILIO_AUTH_TOKEN:          str  = Field(default="")   # also the key for webhook signatures
    TWILIO_PHONE_NUMBER:        str  = Field(default="")
    VERIFY_TWILIO_SIGNATURE:    bool = Field(default=True)
    VERIFY_TWILIO_WS_SIGNATURE: bool = Field(default=True)  # media-stream handshake (legacy voice path)
    OUTBOUND_TTS_VOICE:         str  = Field(default="Polly.Joanna")  # legacy /voice/outbound only

    # ── Meta WhatsApp Cloud API ───────────────────────────
    META_WA_VERIFY_TOKEN:    str = Field(default="")
    META_WA_ACCESS_TOKEN:    str = Field(default="")
    META_WA_PHONE_NUMBER_ID: str = Field(default="")
    META_WABA_ID:            str = Field(default="")
    META_GRAPH_API_VERSION:  str = Field(default="v20.0")
    META_APP_SECRET:         str = Field(default="")   # signs X-Hub-Signature-256 on webhooks

    # ── LiveKit ───────────────────────────────────────────
    LIVEKIT_URL:          str  = Field(default="")
    LIVEKIT_API_KEY:      str  = Field(default="")
    LIVEKIT_API_SECRET:   str  = Field(default="")
    CALL_BASE_URL:        str  = Field(default="")
    LIVEKIT_WORKER_PORT:  int  = Field(default=0)
    LIVEKIT_USE_REALTIME: bool = Field(default=False)

    # ── Conversation Memory ───────────────────────────────
    CONVERSATION_MEMORY_DAYS:         int = Field(default=15)
    CONVERSATION_MEMORY_MAX_MESSAGES: int = Field(default=20)

    # ── Business profile (moves to the tenants collection in Phase 2) ──
    HOTEL_NAME:          str = Field(default="Grand Azure Hotel")
    RECEPTIONIST_NAME:   str = Field(default="Aria")
    HOTEL_PHONE:         str = Field(default="+8801234567890")
    HOTEL_ADDRESS:       str = Field(default="123 Main Street, Dhaka, Bangladesh")
    HOTEL_CHECKIN_TIME:  str = Field(default="14:00")
    HOTEL_CHECKOUT_TIME: str = Field(default="12:00")
    HOTEL_CURRENCY:      str = Field(default="BDT")
    HOTEL_TIMEZONE:      str = Field(default="Asia/Dhaka")

    # ── Security ──────────────────────────────────────────
    SECRET_KEY:          str = Field(default="change-in-production")  # JWTs, call links, ICS tokens
    ADMIN_SECRET_KEY:    str = Field(default="")   # X-Admin-Key for scripts; empty disables it
    ADMIN_USERNAME:      str = Field(default="admin")
    ADMIN_PASSWORD:      str = Field(default="")   # empty => dashboard login disabled
    ADMIN_TOKEN_TTL_MIN: int = Field(default=480)
    # Comma-separated browser origins allowed to call the API. Empty = no cross-origin access.
    CORS_ORIGINS:        str = Field(default="")

    # ── Logging / error tracking ──────────────────────────
    LOG_LEVEL:                 str   = Field(default="INFO")
    LOG_FORMAT:                Literal["json", "text"] = Field(default="json")
    SENTRY_DSN:                str   = Field(default="")
    SENTRY_TRACES_SAMPLE_RATE: float = Field(default=0.1)

    # ── Feature Flags ─────────────────────────────────────
    ENABLE_VOICE_CALLS:              bool = Field(default=True)
    ENABLE_WHATSAPP:                 bool = Field(default=True)
    ENABLE_CALL_RECORDING:           bool = Field(default=False)
    ENABLE_ONE_DAY_BOOKING_REMINDER: bool = Field(default=True)
    BOOKING_REMINDER_POLL_MINUTES:   int  = Field(default=30)

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"

    @model_validator(mode="after")
    def _production_guard(self) -> "Settings":
        if self.APP_ENV != "production":
            return self
        problems = []
        if self.DEBUG:
            problems.append("DEBUG must be false")
        if self.SECRET_KEY in _DEFAULT_SECRETS or len(self.SECRET_KEY) < 32:
            problems.append("SECRET_KEY is weak or default (need 32+ random chars)")
        if self.ADMIN_SECRET_KEY and len(self.ADMIN_SECRET_KEY) < 32:
            problems.append("ADMIN_SECRET_KEY must be empty or 32+ random chars")
        origins = [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]
        if not origins or "*" in origins:
            problems.append("CORS_ORIGINS must list the dashboard origin(s), not '*'")
        if not self.VERIFY_TWILIO_SIGNATURE:
            problems.append("VERIFY_TWILIO_SIGNATURE must be true")
        if not self.WEBHOOK_BASE_URL.startswith("https://"):
            problems.append("WEBHOOK_BASE_URL must be the public https:// URL")
        if self.ENABLE_WHATSAPP and not self.META_APP_SECRET:
            problems.append("META_APP_SECRET is required when ENABLE_WHATSAPP=true")
        if self.LOG_LEVEL.upper() == "DEBUG":
            problems.append("LOG_LEVEL=DEBUG would log transcripts; use INFO")
        if problems:
            raise ValueError("Unsafe production config: " + "; ".join(problems))
        return self


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
