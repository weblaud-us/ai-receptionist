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
    TWILIO_SMS_FROM:            str  = Field(default="")   # platform's registered SMS sender (A2P 10DLC / toll-free)

    # ── Calendar integrations (Phase 2, Task 2.8) ─────────
    GOOGLE_OAUTH_CLIENT_ID:     str = Field(default="")
    GOOGLE_OAUTH_CLIENT_SECRET: str = Field(default="")
    TOKEN_ENCRYPTION_KEY:       str = Field(default="")   # Fernet key(s), comma-separated, newest first
    CALENDAR_SYNC_MINUTES:      int = Field(default=10)   # how often busy times are refreshed

    # ── Background jobs (reminders, digests, calendar sync) ─
    ENABLE_TENANT_JOBS:      bool = Field(default=True)
    TENANT_JOBS_POLL_SECONDS: int = Field(default=300)
    DAILY_DIGEST_HOUR:       int = Field(default=18)      # business-local hour the owner digest goes out
    REQUIRE_ADMIN_MFA:       bool = Field(default=True)   # platform admins must use TOTP two-factor

    # ── Transactional e-mail (Resend) ─────────────────────
    RESEND_API_KEY: str = Field(default="")
    EMAIL_FROM:     str = Field(default="")   # e.g. "Receptionist <hello@yourdomain.com>" — set SPF/DKIM/DMARC first

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

    # ── Voice agent (LiveKit worker, Roadmap Phase 1) ─────
    LIVEKIT_AGENT_NAME:         str = Field(default="receptionist")  # SIP dispatch rule + call links use it
    LIVEKIT_NUM_IDLE_PROCESSES: int = Field(default=1)    # warm spare processes; 2+ in production
    LIVEKIT_SIP_OUTBOUND_TRUNK_ID: str = Field(default="")  # ST_… from `lk sip outbound create` (Phase 4)
    VOICE_LLM_MODEL:            str   = Field(default="gpt-4.1-mini")
    VOICE_LLM_TEMPERATURE:      float = Field(default=0.4)
    VOICE_STT_PROVIDER:         Literal["deepgram", "openai"] = Field(default="deepgram")
    VOICE_STT_MODEL:            str = Field(default="nova-3")
    VOICE_LANGUAGE:             str = Field(default="en-US")  # STT language; "multi" = code-switching
    VOICE_TTS_PROVIDER:         Literal["elevenlabs", "cartesia", "openai"] = Field(default="elevenlabs")
    ELEVENLABS_TTS_MODEL:       str = Field(default="eleven_flash_v2_5")
    CARTESIA_API_KEY:           str = Field(default="")   # fallback TTS when set
    CARTESIA_VOICE_ID:          str = Field(default="")
    CARTESIA_MODEL:             str = Field(default="sonic-3")
    VOICE_ENDPOINTING_MIN_DELAY: float = Field(default=0.3)  # raise if callers get cut off mid-number
    VOICE_ENDPOINTING_MAX_DELAY: float = Field(default=2.5)
    VOICE_USER_AWAY_TIMEOUT:    float = Field(default=12.0)  # seconds of silence before "are you there?"
    VOICE_NOISE_CANCELLATION:   bool  = Field(default=True)  # BVCTelephony on SIP calls (LiveKit Cloud)
    VOICE_THINKING_SOUND:       bool  = Field(default=True)  # soft typing only while a tool runs
    VOICE_AMBIENT_SOUND:        bool  = Field(default=False)
    TRANSFER_PHONE_NUMBER:      str = Field(default="")  # E.164 staff line for transfer_to_human; empty = off
    TRANSFER_HOURS:             str = Field(default="")  # "09:00-21:00" business-local; empty = any time
    OWNER_NOTIFY_WHATSAPP:      str = Field(default="")  # comma-separated numbers told about new messages
    LEGACY_TWILIO_VOICE:        bool = Field(default=True)  # /voice/* Media Streams path; off after cutover

    # Call recording via LiveKit Egress (ENABLE_CALL_RECORDING=true). ⚖ consent-sensitive.
    RECORDING_S3_BUCKET:     str = Field(default="")
    RECORDING_S3_REGION:     str = Field(default="")
    RECORDING_S3_ENDPOINT:   str = Field(default="")   # S3-compatible endpoint; empty = AWS
    RECORDING_S3_ACCESS_KEY: str = Field(default="")
    RECORDING_S3_SECRET:     str = Field(default="")
    RECORDING_RETENTION_DAYS: int = Field(default=30)

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
    ACCESS_TOKEN_TTL_MIN:    int = Field(default=15)    # dashboard access JWT lifetime
    REFRESH_TOKEN_TTL_DAYS:  int = Field(default=30)    # httpOnly refresh cookie / session lifetime
    COOKIE_SECURE:           bool = Field(default=True) # Secure flag on the refresh cookie (false only on http://localhost)
    COOKIE_SAMESITE:         Literal["lax", "strict", "none"] = Field(default="lax")  # "none" if dashboard and API are on different sites
    DASHBOARD_URL:           str = Field(default="")    # public dashboard URL, for invite / password-reset links
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
        if self.ENABLE_CALL_RECORDING and not (self.RECORDING_S3_BUCKET and self.RECORDING_S3_SECRET):
            problems.append("ENABLE_CALL_RECORDING needs RECORDING_S3_BUCKET and credentials")
        if self.GOOGLE_OAUTH_CLIENT_ID and len(self.TOKEN_ENCRYPTION_KEY) < 40:
            problems.append("TOKEN_ENCRYPTION_KEY (a Fernet key) is required with Google Calendar")
        if not self.REQUIRE_ADMIN_MFA:
            problems.append("REQUIRE_ADMIN_MFA must be true")
        if not self.COOKIE_SECURE:
            problems.append("COOKIE_SECURE must be true")
        if self.LOG_LEVEL.upper() == "DEBUG":
            problems.append("LOG_LEVEL=DEBUG would log transcripts; use INFO")
        if problems:
            raise ValueError("Unsafe production config: " + "; ".join(problems))
        return self


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
