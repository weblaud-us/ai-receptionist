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
TTS_PROVIDERS = ("elevenlabs", "cartesia", "deepgram", "kokoro", "openai")


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

    # ── Redis (Phase 3): shared state + job queue ─────────
    # Rate limits, WhatsApp dedupe, per-business call counters and the arq job queue. Empty = each
    # process keeps its own state and the API runs the background jobs itself (one API process only).
    REDIS_URL:               str = Field(default="")   # redis://host:6379/0 or rediss://… (TLS)
    API_RATE_PER_TENANT_MIN: int = Field(default=600)  # dashboard API requests per business per minute; 0 = off
    AUTH_RATE_PER_IP_MIN:    int = Field(default=30)   # sign-in / reset / invite POSTs per IP per minute; 0 = off
    METRICS_TOKEN:           str = Field(default="")   # Bearer token for GET /metrics; empty = off outside development
    # Only on the separate host running fallback_app.py (Twilio's disaster-recovery URL). That app
    # needs no Redis; the full API refuses to start with this set.
    TELEPHONY_FALLBACK_ONLY: bool = Field(default=False)

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
    # Capacity and deploys (Phase 3, Task 3.6)
    LIVEKIT_DRAIN_TIMEOUT:       int   = Field(default=900)   # s a stopping worker waits for live calls to finish
    LIVEKIT_LOAD_THRESHOLD:      float = Field(default=0.7)   # CPU load above which the worker refuses new calls
    LIVEKIT_JOB_MEMORY_WARN_MB:  int   = Field(default=700)   # log a warning when one call's process passes this
    LIVEKIT_JOB_MEMORY_LIMIT_MB: int   = Field(default=2000)  # kill one call's process above this; 0 = never
    LIVEKIT_PROMETHEUS_PORT:     int   = Field(default=0)     # worker's own /metrics port; 0 = off
    VOICE_LLM_MODEL:            str   = Field(default="gpt-4.1-mini")
    VOICE_LLM_TEMPERATURE:      float = Field(default=0.4)
    # Fallbacks (Task 3.5): used automatically when the first provider fails mid-call.
    ANTHROPIC_API_KEY:          str   = Field(default="")   # empty = no fallback LLM
    VOICE_LLM_FALLBACK_MODEL:   str   = Field(default="claude-haiku-5-5")
    VOICE_STT_FALLBACK:         bool  = Field(default=True)  # OpenAI speech-to-text behind Deepgram
    VOICE_STT_PROVIDER:         Literal["deepgram", "openai"] = Field(default="deepgram")
    VOICE_STT_MODEL:            str = Field(default="nova-3")
    VOICE_LANGUAGE:             str = Field(default="en-US")  # STT language; "multi" = code-switching
    # Voice (TTS). VOICE_TTS_PROVIDER is tried first, then VOICE_TTS_FALLBACKS in order; a provider
    # without a key / URL is skipped. Names: elevenlabs | cartesia | deepgram | kokoro | openai.
    VOICE_TTS_PROVIDER:         Literal["elevenlabs", "cartesia", "deepgram", "kokoro", "openai"] = Field(default="elevenlabs")
    VOICE_TTS_FALLBACKS:        str = Field(default="elevenlabs,cartesia,deepgram,kokoro,openai")
    DEEPGRAM_TTS_MODEL:         str = Field(default="aura-2-andromeda-en")  # Aura voice; uses DEEPGRAM_API_KEY
    KOKORO_BASE_URL:            str = Field(default="")   # local Kokoro-FastAPI, e.g. http://kokoro:8880/v1; empty = off
    KOKORO_MODEL:               str = Field(default="kokoro")
    KOKORO_VOICE:               str = Field(default="af_heart")
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

    # Permanent (non-expiring) browser call links: /talk/<business>?k=<key>. See docs/TELEPHONY_TESTING.md Part 3.
    CALL_LINK_VERSION:          int = Field(default=1)    # raise it to revoke every permanent link at once
    PUBLIC_CALL_RATE_PER_IP_HOUR: int = Field(default=10)   # calls one visitor can start per hour (permanent links)
    PUBLIC_CALL_MAX_PER_DAY:    int = Field(default=100)  # calls all visitors together can start per day

    # ── Telephony (SIP into LiveKit) ──────────────────────
    # Empty = decided by APP_ENV: development → softphone (free, Linphone → LiveKit SIP),
    # staging/production → twilio (Elastic SIP Trunking). See docs/TELEPHONY_TESTING.md.
    TELEPHONY_PROVIDER: Literal["", "softphone", "twilio", "livekit"] = Field(default="")
    # livekit = a US number rented in LiveKit Cloud (Telephony -> Phone Numbers); no Twilio, no SIP trunk.
    # softphone (development only)
    SOFTPHONE_TEST_NUMBERS: str = Field(default="+15550000001")  # comma-separated fake "dialled" numbers
    SOFTPHONE_SIP_USERNAME: str = Field(default="")   # a fake caller number, e.g. 15557654321
    SOFTPHONE_SIP_PASSWORD: str = Field(default="")
    # twilio (Elastic SIP trunk ↔ LiveKit trunks)
    TWILIO_SIP_INBOUND_USERNAME:  str = Field(default="")   # empty = Twilio sends no credentials
    TWILIO_SIP_INBOUND_PASSWORD:  str = Field(default="")
    TWILIO_TERMINATION_URI:       str = Field(default="")   # yourname.pstn.twilio.com
    TWILIO_SIP_OUTBOUND_USERNAME: str = Field(default="")   # Twilio trunk credential list
    TWILIO_SIP_OUTBOUND_PASSWORD: str = Field(default="")

    # Call recording via LiveKit Egress (ENABLE_CALL_RECORDING=true). ⚖ consent-sensitive.
    RECORDING_S3_BUCKET:     str = Field(default="")
    RECORDING_S3_REGION:     str = Field(default="")
    RECORDING_S3_ENDPOINT:   str = Field(default="")   # S3-compatible endpoint; empty = AWS
    RECORDING_S3_ACCESS_KEY: str = Field(default="")
    RECORDING_S3_SECRET:     str = Field(default="")
    RECORDING_RETENTION_DAYS: int = Field(default=30)

    # ── Outbound AI sales dialer (Phase 4) ────────────────
    # ⚖ Nothing is dialled until OUTBOUND_DIALER_ENABLED=true AND a campaign is started from the
    # dashboard. Every lead passes the compliance gate (core/outbound/gate.py) before each dial.
    OUTBOUND_DIALER_ENABLED:  bool = Field(default=False)          # master switch; false = the dialer never dials
    SALES_AGENT_NAME:         str  = Field(default="sales-caller")  # the sales worker registers under this name
    SALES_TENANT_ID:          str  = Field(default="")   # your own business as a tenant: its voice, hours, calendar, "Demo" service
    # A trunk used only for sales calls, on its own Twilio subaccount, so a complaint against the
    # dialer can never take customers' inbound numbers down. Outside production it may be empty
    # (LIVEKIT_SIP_OUTBOUND_TRUNK_ID is used); production refuses to dial on the shared trunk.
    SALES_SIP_OUTBOUND_TRUNK_ID: str = Field(default="")
    SALES_COMPANY_NAME:       str  = Field(default="WEBLAUD")
    SALES_PERSONA_NAME:       str  = Field(default="Max")           # the name the sales assistant gives
    SALES_FOUNDER_NAME:       str  = Field(default="")              # who runs the demo
    SALES_DEMO_SERVICE:       str  = Field(default="Demo")          # service name on the sales tenant that demos are booked as
    SALES_DEMO_MEETING_URL:   str  = Field(default="")              # video link put in the demo invite
    SALES_DEMO_LINE:          str  = Field(default="")              # public demo number; also the callback number left on voicemail
    SALES_EMAIL_FOOTER:       str  = Field(default="")              # postal address + opt-out line required on commercial e-mail
    SALES_MAX_CALL_MINUTES:   int  = Field(default=10)              # hard cap on one sales call
    SALES_RING_TIMEOUT_S:     int  = Field(default=25)
    SALES_MAX_CALLS_PER_NUMBER_PER_DAY: int = Field(default=30)     # volume discipline per caller ID (Task 4.1)
    SALES_COST_PER_MINUTE_USD: float = Field(default=0.10)          # estimate used for cost per demo
    # Compliance gate inputs
    TWILIO_LOOKUP_ACCOUNT_SID: str = Field(default="")   # line-type lookups; empty = TWILIO_ACCOUNT_SID
    TWILIO_LOOKUP_AUTH_TOKEN:  str = Field(default="")
    SALES_LINE_TYPE_MAX_AGE_DAYS: int = Field(default=15)   # numbers get ported to mobile; re-check after this
    SALES_DNC_REGISTRY_MAX_AGE_DAYS: int = Field(default=31)  # a national / state list older than this blocks dialling
    SALES_BLOCKED_US_STATES:  str  = Field(default="FL,OK,MD,WA")   # no AI cold calls here until counsel clears each one
    SALES_AI_CONSENTED_COUNTRIES:   str = Field(default="US")       # where a consent record is enough for an AI call
    SALES_HUMAN_ASSISTED_COUNTRIES: str = Field(default="US,CA,GB") # where live (human) B2B calls are allowed after list screening
    # "Get a call from our AI" form (POST /public/demo-call): the running ai_consented campaign new sign-ups join
    SALES_CONSENT_CAMPAIGN_ID: str = Field(default="")
    PUBLIC_DEMO_CALL_PER_IP_HOUR: int = Field(default=3)
    PUBLIC_DEMO_CALL_MAX_PER_DAY: int = Field(default=200)

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

    @property
    def redis_enabled(self) -> bool:
        return bool(self.REDIS_URL.strip())

    @property
    def telephony_provider(self) -> Literal["softphone", "twilio", "livekit"]:
        """Where phone calls come from: TELEPHONY_PROVIDER if set, otherwise APP_ENV decides."""
        if self.TELEPHONY_PROVIDER:
            return self.TELEPHONY_PROVIDER
        return "softphone" if self.is_development else "twilio"

    @property
    def softphone_numbers(self) -> list[str]:
        return [n.strip() for n in self.SOFTPHONE_TEST_NUMBERS.split(",") if n.strip()]

    @staticmethod
    def _codes(raw: str) -> frozenset[str]:
        return frozenset(c.strip().upper() for c in raw.split(",") if c.strip())

    @property
    def sales_blocked_us_states(self) -> frozenset[str]:
        return self._codes(self.SALES_BLOCKED_US_STATES)

    @property
    def sales_ai_consented_countries(self) -> frozenset[str]:
        return self._codes(self.SALES_AI_CONSENTED_COUNTRIES)

    @property
    def sales_human_assisted_countries(self) -> frozenset[str]:
        return self._codes(self.SALES_HUMAN_ASSISTED_COUNTRIES)

    @property
    def sales_trunk_id(self) -> str:
        """The trunk sales calls leave on. Production never falls back to the customers' trunk."""
        if self.SALES_SIP_OUTBOUND_TRUNK_ID or self.is_production:
            return self.SALES_SIP_OUTBOUND_TRUNK_ID
        return self.LIVEKIT_SIP_OUTBOUND_TRUNK_ID

    @property
    def twilio_lookup_credentials(self) -> tuple[str, str]:
        return (self.TWILIO_LOOKUP_ACCOUNT_SID or self.TWILIO_ACCOUNT_SID,
                self.TWILIO_LOOKUP_AUTH_TOKEN or self.TWILIO_AUTH_TOKEN)

    @property
    def tts_fallbacks(self) -> list[str]:
        return [n.strip().lower() for n in self.VOICE_TTS_FALLBACKS.split(",") if n.strip()]

    def tts_available(self, name: str) -> bool:
        """True when the provider has what it needs (a key, or Kokoro's URL)."""
        return bool({
            "elevenlabs": self.ELEVENLABS_API_KEY,
            "cartesia": self.CARTESIA_API_KEY,
            "deepgram": self.DEEPGRAM_API_KEY,
            "kokoro": self.KOKORO_BASE_URL,
            "openai": self.OPENAI_API_KEY,
        }.get(name))

    def tts_order(self, primary: str) -> list[str]:
        """Voices to try, best first: `primary` (a business's own choice), then VOICE_TTS_PROVIDER,
        then VOICE_TTS_FALLBACKS. Duplicates and unknown names are dropped; availability is not
        checked here (see `tts_available`)."""
        order: list[str] = []
        for name in (primary, self.VOICE_TTS_PROVIDER, *self.tts_fallbacks):
            if name in TTS_PROVIDERS and name not in order:
                order.append(name)
        return order

    @model_validator(mode="after")
    def _known_tts_fallbacks(self) -> "Settings":
        unknown = [n for n in self.tts_fallbacks if n not in TTS_PROVIDERS]
        if unknown:
            raise ValueError(f"VOICE_TTS_FALLBACKS has unknown voice(s) {unknown}; use {', '.join(TTS_PROVIDERS)}")
        return self

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
        if self.telephony_provider == "softphone":
            problems.append("TELEPHONY_PROVIDER=softphone is not allowed in production")
        if self.LOG_LEVEL.upper() == "DEBUG":
            problems.append("LOG_LEVEL=DEBUG would log transcripts; use INFO")
        if not self.redis_enabled and not self.TELEPHONY_FALLBACK_ONLY:
            problems.append("REDIS_URL is required (shared rate limits, call limits and the job queue)")
        if self.METRICS_TOKEN and len(self.METRICS_TOKEN) < 24:
            problems.append("METRICS_TOKEN must be empty or 24+ random chars")
        if self.OUTBOUND_DIALER_ENABLED:
            if not self.SALES_SIP_OUTBOUND_TRUNK_ID:
                problems.append("OUTBOUND_DIALER_ENABLED needs SALES_SIP_OUTBOUND_TRUNK_ID (a trunk used only for sales calls)")
            elif self.SALES_SIP_OUTBOUND_TRUNK_ID == self.LIVEKIT_SIP_OUTBOUND_TRUNK_ID:
                problems.append("SALES_SIP_OUTBOUND_TRUNK_ID must not be the customers' trunk (LIVEKIT_SIP_OUTBOUND_TRUNK_ID)")
            if not self.SALES_TENANT_ID:
                problems.append("OUTBOUND_DIALER_ENABLED needs SALES_TENANT_ID")
            if not all(self.twilio_lookup_credentials):
                problems.append("OUTBOUND_DIALER_ENABLED needs Twilio credentials for line-type lookups")
        if problems:
            raise ValueError("Unsafe production config: " + "; ".join(problems))
        return self


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
