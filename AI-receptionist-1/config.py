"""
LaudLine — configuration.

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


# Regional stacks (deploy/README.md → "Regional stacks").
REGION_COUNTRIES = {"ap-southeast-2": "AU", "eu-west-2": "GB"}
KNOWN_DEPLOYMENT_REGIONS = frozenset({"us-east-1", "us-west-2", "eu-west-1", "eu-central-1", *REGION_COUNTRIES})
# Twilio edge locations and the data region they front by default (⚠ VERIFY against Twilio's list).
TWILIO_EDGE_REGIONS = {"sydney": "au1", "dublin": "ie1", "ashburn": "us1", "umatilla": "us1", "frankfurt": "ie1",
                       "tokyo": "jp1", "singapore": "us1", "sao-paulo": "us1"}
TWILIO_REGION_EDGES = {"au1": "sydney", "ie1": "dublin", "jp1": "tokyo"}


def _url_has_password(url: str) -> bool:
    from urllib.parse import urlparse

    try:
        return bool(urlparse(url.strip()).password)
    except ValueError:
        return False


def _fernet_keys_ok(value: str) -> bool:
    keys = [k.strip() for k in (value or "").split(",") if k.strip()]
    if not keys:
        return False
    try:
        from cryptography.fernet import Fernet

        for k in keys:
            Fernet(k.encode())
    except Exception:
        return False
    return True


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
    # Connections per process (API replica, worker, jobs). Size for Atlas's connection limit:
    # replicas × processes × max must stay under it (audit SRE-10).
    MONGODB_MAX_POOL: int = Field(default=50, ge=1, le=500)

    # ── Redis (Phase 3): shared state + job queue ─────────
    # Rate limits, WhatsApp dedupe, per-business call counters and the arq job queue. Empty = each
    # process keeps its own state and the API runs the background jobs itself (one API process only).
    REDIS_URL:               str = Field(default="")   # redis://host:6379/0 or rediss://… (TLS)
    # Read by the bundled Redis container (deploy/docker-compose.prod.yml requirepass), not the app,
    # which uses the password inside REDIS_URL. Keep the two the same.
    REDIS_PASSWORD:          str = Field(default="")
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

    # ── Processor data use (UK GDPR Art. 9 / Australian Privacy Principles) ──
    # Deepgram: opt every request out of its Model Improvement Program (no training on our audio).
    DEEPGRAM_MIP_OPT_OUT: bool = Field(default=True)
    # OpenAI: never store completions for evals/distillation (the `store` request flag).
    OPENAI_STORE: bool = Field(default=False)
    # Set true only once the vendor has approved Zero Data Retention for this account. The code can't
    # switch ZDR on; these flags record that it is, for the DPA, the sub-processor list and
    # scripts/check_env.py. Never claim ZDR to a customer while one is false.
    OPENAI_ZDR_ENABLED: bool = Field(default=False)
    DEEPGRAM_ZDR_ENABLED: bool = Field(default=False)
    # ElevenLabs zero-retention mode is a request flag (enable_logging=false) that only enterprise
    # accounts may send; true = send it, so nothing a caller hears is kept by ElevenLabs.
    ELEVENLABS_ZDR_ENABLED: bool = Field(default=False)

    # ── Regional deployment (AU / UK dental stacks) ────────────────────────
    # The AWS region this stack's data lives in: ap-southeast-2 (Sydney) for AU practices, eu-west-2
    # (London) for UK practices. One stack per region; see deploy/README.md → "Regional stacks".
    DEPLOYMENT_REGION: str = Field(default="")
    # Twilio edge (where our API traffic enters Twilio) and Twilio region (where Twilio keeps the
    # data). sydney → au1, dublin → ie1. A non-US region needs that region's own API credentials.
    TWILIO_EDGE: str = Field(default="")
    TWILIO_REGION: str = Field(default="")

    # ── Dental practice-management systems (core/integrations/cliniko.py, dentally.py) ──
    # Cliniko requires a User-Agent naming the app and a contact address, e.g. "LaudLine (ops@yourco.com)".
    CLINIKO_USER_AGENT: str = Field(default="")
    # The shard used when a practice's API key carries none (keys normally end in -au1, -au2, -uk1).
    CLINIKO_SHARD: str = Field(default="au1")
    # Dentally access needs an approved partner application; leave off until it is and the adapter has
    # been checked against the Dentally sandbox.
    PMS_DENTALLY_ENABLED: bool = Field(default=False)
    DENTALLY_API_BASE_URL: str = Field(default="https://api.dentally.co/v1")

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
    EMAIL_FROM:     str = Field(default="")   # e.g. "LaudLine <hello@yourdomain.com>" — set SPF/DKIM/DMARC first

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
    # How long a job process may take to start (load the VAD, import the agent). LiveKit's own 10 s is too
    # tight on a busy dev machine: processes are killed mid-start in a loop and no call is ever taken.
    LIVEKIT_INITIALIZE_PROCESS_TIMEOUT: float = Field(default=30.0, ge=5, le=120)
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
    # Cartesia Sonic-3 is the platform's voice; Deepgram Aura (same key as the STT) is the fallback.
    VOICE_TTS_PROVIDER:         Literal["elevenlabs", "cartesia", "deepgram", "kokoro", "openai"] = Field(default="cartesia")
    VOICE_TTS_FALLBACKS:        str = Field(default="deepgram")
    # Deepgram voice (the model name is the voice): Flux TTS "flux-<voice>-en" (/v2/speak) or Aura
    # "aura-2-<voice>-en" (/v1/speak). Uses DEEPGRAM_API_KEY. _AU / _GB: per-country, empty = this one.
    DEEPGRAM_TTS_MODEL:         str = Field(default="flux-hannah-en")
    DEEPGRAM_TTS_MODEL_AU:      str = Field(default="")
    DEEPGRAM_TTS_MODEL_GB:      str = Field(default="")
    KOKORO_BASE_URL:            str = Field(default="")   # local Kokoro-FastAPI, e.g. http://kokoro:8880/v1; empty = off
    KOKORO_MODEL:               str = Field(default="kokoro")
    KOKORO_VOICE:               str = Field(default="af_heart")
    # eleven_v4_turbo: ElevenLabs' most expressive real-time model (text-to-dialogue socket).
    # eleven_flash_v2_5 is the faster, flatter choice if v4's first byte is too slow from AU / UK.
    ELEVENLABS_TTS_MODEL:       str = Field(default="eleven_v4_turbo")
    # Raw 24 kHz PCM: no 32 kbps MP3 artefacts (the plugin's default) and no decode step.
    ELEVENLABS_ENCODING:        str = Field(default="pcm_24000")
    ELEVENLABS_STABILITY:       float = Field(default=0.0, ge=0.0, le=1.0)  # 0 = the voice's own setting
    ELEVENLABS_SPEED:           float = Field(default=0.0, ge=0.0, le=1.2)  # 0 = normal; Flash/Turbo only (0.8–1.2)
    # The accent-matched voice a practice gets when it hasn't picked its own (scripts/voice_bakeoff.py).
    ELEVENLABS_VOICE_ID_AU:     str = Field(default="")
    ELEVENLABS_VOICE_ID_GB:     str = Field(default="")
    CARTESIA_API_KEY:           str = Field(default="")   # the main voice
    CARTESIA_VOICE_ID:          str = Field(default="")
    # The accent-matched voice a practice gets when it hasn't picked its own (scripts/voice_bakeoff.py).
    CARTESIA_VOICE_ID_AU:       str = Field(default="")
    CARTESIA_VOICE_ID_GB:       str = Field(default="")
    CARTESIA_MODEL:             str = Field(default="sonic-3")
    CARTESIA_SPEED:             float = Field(default=0.0, ge=0.0, le=2.0)  # 0 = normal; Sonic-3 takes 0.6–2.0
    # Turn-taking (Vapi/Retell-grade feel). livekit-agents picks the small local models when running
    # `start` on our own hosts, so these are set explicitly:
    #   v1 = LiveKit's cloud end-of-turn model (falls back to the local v1-mini if the gateway fails)
    VOICE_TURN_DETECTOR:        Literal["v1", "v1-mini"] = Field(default="v1")
    # adaptive = ML barge-in: stops for "wait"/"no", talks through the caller's "mm-hmm" (audit P0-13).
    # Falls back to plain VAD by itself if the detector can't start.
    VOICE_INTERRUPT_MODE:       Literal["adaptive", "vad"] = Field(default="adaptive")
    VOICE_INTERRUPT_MIN_DURATION: float = Field(default=0.25, ge=0.1, le=1.5)  # s of speech; "wait!" is ~0.25 s
    VOICE_PREEMPTIVE_TTS:       bool  = Field(default=True)  # start the voice before the turn is confirmed
    VOICE_ENDPOINTING_MIN_DELAY: float = Field(default=0.3)  # raise if callers get cut off mid-number
    VOICE_ENDPOINTING_MAX_DELAY: float = Field(default=2.5)
    VOICE_USER_AWAY_TIMEOUT:    float = Field(default=12.0)  # seconds of silence before "are you there?"
    VOICE_NOISE_CANCELLATION:   bool  = Field(default=True)  # BVCTelephony on SIP calls (LiveKit Cloud)
    VOICE_THINKING_SOUND:       bool  = Field(default=True)  # soft typing only while a tool runs
    VOICE_AMBIENT_SOUND:        bool  = Field(default=False)
    TRANSFER_PHONE_NUMBER:      str = Field(default="")  # E.164 staff line for transfer_to_human; empty = off
    TRANSFER_HOURS:             str = Field(default="")  # "09:00-21:00" business-local; empty = any time
    OWNER_NOTIFY_WHATSAPP:      str = Field(default="")  # comma-separated numbers told about new messages
    # The old /voice/* Twilio Media Streams path (slow, one API replica only). Off by default: phone
    # calls go through the LiveKit SIP trunk. Turn on only for a number not yet moved (Task 1.13).
    LEGACY_TWILIO_VOICE:        bool = Field(default=False)

    # Permanent (non-expiring) browser call links: /talk/<business>?k=<key>. See docs/TELEPHONY_TESTING.md Part 3.
    CALL_LINK_VERSION:          int = Field(default=1)    # raise it to revoke every permanent link at once
    PUBLIC_CALL_RATE_PER_IP_HOUR: int = Field(default=10)   # calls one visitor can start per hour (permanent links)
    PUBLIC_CALL_MAX_PER_DAY:    int = Field(default=100)  # calls all visitors together can start per day

    # Landing-page live demo ("Talk to it now"): a visitor signs in with Google, then talks to a demo
    # practice in the browser. Each visitor becomes a lead in the platform dashboard (Demo leads).
    # Off until the demo practices exist: uv run python -m scripts.seed_web_demo
    WEB_DEMO_ENABLED:           bool = Field(default=False)
    WEB_DEMO_TENANT_AU:         str  = Field(default="")   # tenant id of the fictional AU demo practice
    WEB_DEMO_TENANT_GB:         str  = Field(default="")   # tenant id of the fictional UK demo practice
    WEB_DEMO_MAX_SECONDS:       int  = Field(default=180, ge=30, le=900)  # each demo call ends after this
    WEB_DEMO_PER_ACCOUNT_DAY:   int  = Field(default=5)    # demos one Google account can start per day
    WEB_DEMO_PER_IP_HOUR:       int  = Field(default=10)
    WEB_DEMO_MAX_PER_DAY:       int  = Field(default=300)  # all visitors together (cost ceiling)
    WEB_DEMO_LEAD_RETENTION_DAYS: int = Field(default=365, ge=30)
    WEB_DEMO_DIARY_RETENTION_HOURS: int = Field(default=24, ge=1)  # demo practices' bookings, contacts, messages
    WEB_DEMO_TRANSCRIPT_DAYS:   int  = Field(default=30, ge=1)  # demo calls (read in Demo leads), then deleted
    # The Google OAuth web client the landing page's "Sign in with Google" button uses (its JavaScript
    # origins must include the landing site). Empty = GOOGLE_OAUTH_CLIENT_ID (the calendar client).
    GOOGLE_SIGNIN_CLIENT_ID:    str  = Field(default="")

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

    # The platform does not send SMS (2026-11-23). Patient and owner messages go by WhatsApp or e-mail;
    # missed-call text-back and SMS recall are off. Turning this on is a deliberate product decision
    # (Spam Act / PECR consent rules apply to every automated text).
    SMS_ENABLED: bool = Field(default=False)
    # Call audio is never recorded (2026-11-23: transcripts only). These settings remain only so the
    # retention, erasure and offboarding jobs can still delete audio stored before that change.
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
    SALES_COMPANY_NAME:       str  = Field(default="LaudLine")
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
    # "Not interested" (not an opt-out): that offer is never pitched to them again, and no other
    # offer may call them for this many days (SALES_DIALER_PLAN.md Phase 1A).
    SALES_NOT_INTERESTED_COOLDOWN_DAYS: int = Field(default=180)
    # Across campaigns: a prospect is rung by one campaign at most this soon after another one called,
    # and at most this many times in 30 days by all of them (a callback they asked for is exempt).
    SALES_MIN_HOURS_BETWEEN_CAMPAIGNS: int = Field(default=24)
    SALES_MAX_CALLS_PER_PROSPECT_30D: int = Field(default=6)
    # Pre-call research (Phase 2): read each lead's website before the call. Off until switched on;
    # capped per day. Prices are OPENAI_MODEL's (gpt-4o-mini) per million tokens.
    SALES_RESEARCH_ENABLED:   bool  = Field(default=False)
    SALES_RESEARCH_TTL_DAYS:  int   = Field(default=30)
    SALES_RESEARCH_DAILY_USD: float = Field(default=5.0)
    SALES_RESEARCH_USD_PER_1M_IN:  float = Field(default=0.15)
    SALES_RESEARCH_USD_PER_1M_OUT: float = Field(default=0.60)
    # AI offer copilot (SALES_SUITE_PLAN.md): draft, assist, review, insights and test-call simulations.
    # It only ever suggests; a person saves and publishes. Capped per day across every admin.
    SALES_AI_ASSIST_ENABLED:  bool  = Field(default=True)
    SALES_AI_ASSIST_DAILY_USD: float = Field(default=3.0)
    SALES_AI_ASSIST_MODEL:    str   = Field(default="")              # empty = OPENAI_MODEL
    SALES_AI_ASSIST_PER_USER_HOUR: int = Field(default=60)
    # The agent that learns from its calls (SALES_LEARNING_PLAN.md). Decisions are made in code from
    # outcomes; the AI only writes coaching, which is tested before and during use.
    SALES_LEAD_MEMORY_ENABLED: bool = Field(default=True)           # the agent knows its earlier calls with a business
    SALES_MEMORY_DAYS:        int   = Field(default=180, ge=1, le=730)
    SALES_COACH_ENABLED:      bool  = Field(default=True)            # an AI coach reviews every AI call afterwards
    SALES_COACH_BATCH:        int   = Field(default=5, ge=1, le=50)  # reviews per learning tick
    SALES_LEARNING_MODEL:     str   = Field(default="")              # empty = SALES_AI_ASSIST_MODEL, then OPENAI_MODEL
    SALES_LEARNING_DAILY_USD: float = Field(default=2.0)             # its own budget: never eats the copilot's
    SALES_LEARNING_USD_PER_1M_IN:  float = Field(default=0.15)
    SALES_LEARNING_USD_PER_1M_OUT: float = Field(default=0.60)
    SALES_REVIEW_RETENTION_DAYS: int = Field(default=90, ge=7, le=730)   # the coach's notes; scores stay
    SALES_PLAYBOOKS_ENABLED:  bool  = Field(default=True)            # false = every call uses the reviewed baseline
    SALES_LEARNING_AUTOPILOT: bool  = Field(default=True)            # the AI may write the next playbook to test
    SALES_LEARNING_MIN_REVIEWS: int = Field(default=20, ge=5, le=1000)
    SALES_EXPERIMENT_SHARE:   float = Field(default=0.33, ge=0.1, le=0.5)   # calls that try the new playbook
    SALES_EXPERIMENT_MAX_DAYS: int  = Field(default=45, ge=7, le=180)
    SALES_SMART_RETRY_MAX_DELAY_H: int = Field(default=24, ge=1, le=168)    # how far a retry may move for a better hour
    # Launch safety: a campaign can't start until its offer version passed the simulated test calls.
    SALES_REQUIRE_SIMULATION: bool  = Field(default=True)
    SALES_ALERT_EMAILS:       str   = Field(default="")              # comma-separated: auto-pause and number alerts
    SALES_DIGEST_HOUR_UTC:    int   = Field(default=21, ge=0, le=23)  # daily "your sales day" email (21 UTC = 8 am Sydney)
    # Built-in CRM: replies to sales email (reply+<ref>@domain) via an inbound-parse webhook. Off until set.
    SALES_INBOUND_DOMAIN:     str   = Field(default="")
    SALES_INBOUND_EMAIL_SECRET: str = Field(default="")
    # Compliance gate inputs
    TWILIO_LOOKUP_ACCOUNT_SID: str = Field(default="")   # line-type lookups; empty = TWILIO_ACCOUNT_SID
    TWILIO_LOOKUP_AUTH_TOKEN:  str = Field(default="")
    SALES_LINE_TYPE_MAX_AGE_DAYS: int = Field(default=15)   # numbers get ported to mobile; re-check after this
    SALES_DNC_REGISTRY_MAX_AGE_DAYS: int = Field(default=31)  # a national / state list older than this blocks dialling
    SALES_BLOCKED_US_STATES:  str  = Field(default="FL,OK,MD,WA")   # no AI cold calls here until counsel clears each one
    SALES_AI_CONSENTED_COUNTRIES:   str = Field(default="US,AU")    # where a consent record is enough for an AI call
    SALES_HUMAN_ASSISTED_COUNTRIES: str = Field(default="US,CA,GB,AU")  # where live (human) B2B calls are allowed after list screening
    # AI cold calls to business landlines. US only until counsel signs off another country (AU: is an
    # AI voice a "recorded message"?). A country here needs a calling window and a do-not-call list.
    SALES_AI_COLD_COUNTRIES:  str = Field(default="US")
    SALES_AU_EXTRA_BLOCKED_DATES: str = Field(default="")             # ISO dates, comma-separated: no AU calls on these
    SALES_AU_DNCR_WASH_MAX_AGE_DAYS: int = Field(default=30)          # a DNCR wash result is good for 30 days
    # "Get a call from our AI" form (POST /public/demo-call): the running ai_consented campaign new sign-ups join
    SALES_CONSENT_CAMPAIGN_ID: str = Field(default="")
    PUBLIC_DEMO_CALL_PER_IP_HOUR: int = Field(default=3)
    PUBLIC_DEMO_CALL_MAX_PER_DAY: int = Field(default=200)

    # ── Billing and self-serve signup (Phase 5) ───────────
    # Stripe is called over its REST API (core/billing/stripe_api.py). Empty STRIPE_SECRET_KEY =
    # billing off: sign-ups start a local trial and nothing is charged (development only).
    STRIPE_SECRET_KEY:       str  = Field(default="")   # sk_live_… / sk_test_… (restricted keys work)
    STRIPE_WEBHOOK_SECRET:   str  = Field(default="")   # whsec_… for POST /billing/stripe/webhook
    STRIPE_PRICE_STARTER_MONTHLY: str = Field(default="")
    STRIPE_PRICE_STARTER_ANNUAL:  str = Field(default="")
    STRIPE_PRICE_GROWTH_MONTHLY:  str = Field(default="")
    STRIPE_PRICE_GROWTH_ANNUAL:   str = Field(default="")
    STRIPE_PRICE_PRO_MONTHLY:     str = Field(default="")
    STRIPE_PRICE_PRO_ANNUAL:      str = Field(default="")
    STRIPE_PRICE_OVERAGE:    str  = Field(default="")   # metered price on the Billing Meter below
    STRIPE_METER_EVENT_NAME: str  = Field(default="overage_minutes")
    STRIPE_PRICE_SETUP_FEE:  str  = Field(default="")   # optional one-off "done-for-you setup" price
    # Dental plans (AU/UK practices): retainer Prices with AUD/GBP currency_options, and their own
    # mandatory one-time onboarding-fee Prices (core/billing/plans.DENTAL_SETUP_FEES).
    STRIPE_PRICE_DENTAL_ESSENTIALS_MONTHLY: str = Field(default="")
    STRIPE_PRICE_DENTAL_ESSENTIALS_ANNUAL:  str = Field(default="")
    STRIPE_PRICE_DENTAL_PRO_MONTHLY:        str = Field(default="")
    STRIPE_PRICE_DENTAL_PRO_ANNUAL:         str = Field(default="")
    STRIPE_PRICE_DENTAL_SETUP_ESSENTIALS:   str = Field(default="")
    STRIPE_PRICE_DENTAL_SETUP_PRO:          str = Field(default="")
    STRIPE_AUTOMATIC_TAX:    bool = Field(default=True)  # Stripe Tax: US sales tax, EU/UK VAT, UAE VAT
    SIGNUP_ENABLED:          bool = Field(default=False)  # public POST /signup
    SIGNUP_RATE_PER_IP_HOUR: int  = Field(default=5)
    TRIAL_DAYS:              int  = Field(default=14)
    TRIAL_MINUTES:           int  = Field(default=50)    # hard cap during the trial (toll-fraud protection)
    PAST_DUE_SUSPEND_DAYS:   int  = Field(default=7)     # unpaid this long → suspended (calls forwarded)
    CANCEL_RETENTION_DAYS:   int  = Field(default=30)    # data kept (reactivation possible), then deleted
    TERMS_URL:               str  = Field(default="")
    PRIVACY_URL:             str  = Field(default="")

    # ── Legal and compliance (Phase 6) ────────────────────
    # Where the published documents live. The texts are edited in the platform dashboard (Legal documents)
    # and shown by the marketing site at <LEGAL_SITE_URL>/legal/<key>; a *_URL below overrides one document.
    LEGAL_SITE_URL:          str  = Field(default="")   # the marketing site, e.g. https://laudline.com
    SUBSCRIPTION_URL:        str  = Field(default="")
    COOKIES_URL:             str  = Field(default="")
    DPA_URL:                 str  = Field(default="")
    AUP_URL:                 str  = Field(default="")
    SLA_URL:                 str  = Field(default="")
    SECURITY_URL:            str  = Field(default="")
    SUBPROCESSORS_URL:       str  = Field(default="")
    LEGAL_ENTITY_NAME:       str  = Field(default="WEBLAUD LLC")
    PRIVACY_CONTACT_EMAIL:   str  = Field(default="")   # where data-protection requests go (privacy policy, DPA)
    DSR_DEADLINE_DAYS:       int  = Field(default=30)   # GDPR: one month; shown as the due date of each request
    # Inbound SMS (Twilio "A message comes in" webhook → POST /telephony/sms/inbound). STOP/START are
    # recorded here; set false when a Twilio Messaging Service with Advanced Opt-Out replies itself.
    SMS_OPTOUT_REPLY:        bool = Field(default=True)

    # ── Launch, pilots and support (Phase 7) ──────────────
    DEMO_PHONE_NUMBER:       str  = Field(default="")   # the live demo line printed on the website (GET /public/site)
    STATUS_PAGE_URL:         str  = Field(default="")   # hosted status page (separate from this server)
    SUPPORT_EMAIL:           str  = Field(default="")
    HELP_CENTER_URL:         str  = Field(default="")
    MONTHLY_REPORT_ENABLED:  bool = Field(default=True)  # "your assistant this month" e-mail on the 1st
    REPORT_HOUR:             int  = Field(default=9)     # business-local hour for the monthly and weekly pilot reports
    PILOT_REPORT_BCC:        str  = Field(default="")   # you, on every weekly pilot report
    REVIEW_SAMPLE_PCT:       int  = Field(default=20)   # share of non-pilot calls in the review queue (pilots: all)

    # ── Number provisioning (Phase 5, Task 5.4) ───────────
    # New numbers are bought on Twilio, attached to the Elastic SIP trunk and added to the LiveKit
    # inbound trunk. With TELEPHONY_PROVIDER=softphone (development) a fake +1555 number is mapped instead.
    NUMBER_PROVISIONING_ENABLED: bool = Field(default=False)
    TWILIO_SIP_TRUNK_SID:        str  = Field(default="")   # TK… the trunk new numbers attach to
    LIVEKIT_SIP_INBOUND_TRUNK_ID: str = Field(default="")   # ST_… inbound trunk whose `numbers` list grows
    NUMBER_INSTANT_COUNTRIES:    str  = Field(default="US,CA")   # elsewhere needs a regulatory bundle first
    NUMBER_VERIFY_CALL:          bool = Field(default=True)  # synthetic call before a number is marked active
    # Transfers to these prefixes are refused: premium-rate, revenue-sharing and satellite ranges that
    # toll fraud uses (US/CA, UK, IE, MT, AU, FR, DE, AT, ES, IT, NL, BE, AE, international networks).
    # ⚠ VERIFY against each regulator's current numbering plan before launch.
    TRANSFER_BLOCKED_PREFIXES:   str  = Field(default="+1900,+1976,+449,+4487,+44118,+4470,+35315,+35650,+61190,+3389,+3381,+3382,+49900,+49137,+43900,+43930,+43939,+34803,+34806,+34807,+34905,+39899,+39892,+31906,+31909,+32900,+32902,+32903,+32904,+32905,+32906,+32907,+32909,+971900,+870,+881,+882,+883,+979")

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
    # Audit SEC-01: trust CF-Connecting-IP only when Cloudflare really is in front (and the origin
    # firewall accepts only Cloudflare's ranges). Otherwise the client address is uvicorn's, from the
    # X-Forwarded-For our own proxy sets (FORWARDED_ALLOW_IPS = the private network it runs on).
    BEHIND_CLOUDFLARE:   bool = Field(default=False)
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
    PLATFORM_DASHBOARD_URL:  str = Field(default="")    # the platform (admin) dashboard, for sales-team invite links
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
    def sales_alert_emails(self) -> list[str]:
        return [e.strip() for e in self.SALES_ALERT_EMAILS.split(",") if "@" in e]

    @property
    def sales_ai_consented_countries(self) -> frozenset[str]:
        return self._codes(self.SALES_AI_CONSENTED_COUNTRIES)

    @property
    def sales_human_assisted_countries(self) -> frozenset[str]:
        return self._codes(self.SALES_HUMAN_ASSISTED_COUNTRIES)

    @property
    def sales_ai_cold_countries(self) -> frozenset[str]:
        return self._codes(self.SALES_AI_COLD_COUNTRIES)

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
    def billing_enabled(self) -> bool:
        return bool(self.STRIPE_SECRET_KEY.strip())

    @property
    def number_instant_countries(self) -> frozenset[str]:
        return self._codes(self.NUMBER_INSTANT_COUNTRIES)

    @property
    def transfer_blocked_prefixes(self) -> tuple[str, ...]:
        return tuple(p.strip() for p in self.TRANSFER_BLOCKED_PREFIXES.split(",") if p.strip().startswith("+"))

    @property
    def google_signin_client_id(self) -> str:
        return (self.GOOGLE_SIGNIN_CLIENT_ID or self.GOOGLE_OAUTH_CLIENT_ID).strip()

    def web_demo_tenant(self, region: str) -> str:
        """The demo practice for a landing-page region ("au" / "uk"); "" = none set up."""
        return {"au": self.WEB_DEMO_TENANT_AU, "uk": self.WEB_DEMO_TENANT_GB,
                "gb": self.WEB_DEMO_TENANT_GB}.get((region or "").lower(), "").strip()

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

    @property
    def twilio_api_base(self) -> str:
        """https://api.twilio.com, or the edge/region host (https://api.sydney.au1.twilio.com)."""
        edge, region = self.TWILIO_EDGE.strip().lower(), self.TWILIO_REGION.strip().lower()
        if edge in TWILIO_REGION_EDGES:                 # "ie1" / "au1" given as the edge: use the region's edge
            region, edge = edge, TWILIO_REGION_EDGES[edge]
        if not edge and not region:
            return "https://api.twilio.com"
        edge = edge or TWILIO_REGION_EDGES.get(region, "ashburn")
        region = region or TWILIO_EDGE_REGIONS.get(edge, "us1")
        return f"https://api.{edge}.{region}.twilio.com"

    @property
    def deployment_country(self) -> str:
        """The country whose practices this stack is for ("" = not a regional stack)."""
        return REGION_COUNTRIES.get(self.DEPLOYMENT_REGION.strip(), "")

    @model_validator(mode="after")
    def _known_tts_fallbacks(self) -> "Settings":
        unknown = [n for n in self.tts_fallbacks if n not in TTS_PROVIDERS]
        if unknown:
            raise ValueError(f"VOICE_TTS_FALLBACKS has unknown voice(s) {unknown}; use {', '.join(TTS_PROVIDERS)}")
        fmt, _, rate = self.ELEVENLABS_ENCODING.partition("_")
        if fmt not in ("pcm", "mp3", "opus") or not rate.split("_")[0].isdigit():
            raise ValueError(f"ELEVENLABS_ENCODING={self.ELEVENLABS_ENCODING!r}: use e.g. pcm_24000 or mp3_44100_128")
        # An AI cold-call country needs its legal calling hours and do-not-call list encoded first.
        unruled = sorted(c for c in self.sales_ai_cold_countries if c not in ("US", "AU", "CA", "GB"))
        if unruled:
            raise ValueError(f"SALES_AI_COLD_COUNTRIES has {unruled}: add a calling window and do-not-call list "
                             "for it in core/outbound/rules.py first (and get counsel's sign-off)")
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
        # Audit SEC-16: TOTP secrets, PMS keys and sealed patient fields are encrypted with it. No key
        # derived from SECRET_KEY in production: rotating SECRET_KEY would make all of them unreadable.
        if not _fernet_keys_ok(self.TOKEN_ENCRYPTION_KEY):
            problems.append("TOKEN_ENCRYPTION_KEY must be set to a Fernet key (comma-separated list for rotation)")
        if not self.REQUIRE_ADMIN_MFA:
            problems.append("REQUIRE_ADMIN_MFA must be true")
        if not self.DEEPGRAM_MIP_OPT_OUT:
            problems.append("DEEPGRAM_MIP_OPT_OUT must be true (callers' health details must not train Deepgram models)")
        if self.OPENAI_STORE:
            problems.append("OPENAI_STORE must be false (calls must not be stored by OpenAI)")
        if self.LEGACY_TWILIO_VOICE:
            problems.append("LEGACY_TWILIO_VOICE must be false: phone calls go through the LiveKit SIP trunk "
                            "(the old path is the hotel stack, runs on one replica and drops calls on deploy)")
        if self.TWILIO_EDGE.strip() and self.TWILIO_EDGE.strip().lower() not in {*TWILIO_EDGE_REGIONS, *TWILIO_REGION_EDGES}:
            problems.append(f"TWILIO_EDGE {self.TWILIO_EDGE!r} is not a Twilio edge (sydney, dublin, …)")
        if self.DEPLOYMENT_REGION.strip() and self.DEPLOYMENT_REGION.strip() not in KNOWN_DEPLOYMENT_REGIONS:
            problems.append(f"DEPLOYMENT_REGION {self.DEPLOYMENT_REGION!r} is not a supported region")
        if not self.COOKIE_SECURE:
            problems.append("COOKIE_SECURE must be true")
        if self.telephony_provider == "softphone":
            problems.append("TELEPHONY_PROVIDER=softphone is not allowed in production")
        if self.LOG_LEVEL.upper() == "DEBUG":
            problems.append("LOG_LEVEL=DEBUG would log transcripts; use INFO")
        if not self.redis_enabled and not self.TELEPHONY_FALLBACK_ONLY:
            problems.append("REDIS_URL is required (shared rate limits, call limits and the job queue)")
        if self.redis_enabled and not _url_has_password(self.REDIS_URL):
            problems.append("REDIS_URL needs a password (redis://:<REDIS_PASSWORD>@redis:6379/0), audit SRE-04")
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
        if self.SIGNUP_ENABLED:
            if not (self.billing_enabled and self.STRIPE_WEBHOOK_SECRET):
                problems.append("SIGNUP_ENABLED needs STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET")
            if not (self.STRIPE_PRICE_STARTER_MONTHLY or self.STRIPE_PRICE_GROWTH_MONTHLY
                    or self.STRIPE_PRICE_PRO_MONTHLY):
                problems.append("SIGNUP_ENABLED needs at least one STRIPE_PRICE_*_MONTHLY")
            if not (self.RESEND_API_KEY and self.EMAIL_FROM):
                problems.append("SIGNUP_ENABLED needs e-mail (RESEND_API_KEY, EMAIL_FROM) for verification")
            if not self.DASHBOARD_URL.startswith("https://"):
                problems.append("SIGNUP_ENABLED needs DASHBOARD_URL (https) for Checkout return links")
            # Phase 6: nobody signs up to terms they can't read (docs/COMPLIANCE.md §11.8).
            missing_legal = [k for k in ("TERMS_URL", "PRIVACY_URL", "DPA_URL")
                             if not (getattr(self, k) or self.LEGAL_SITE_URL)]
            if missing_legal:
                problems.append(f"SIGNUP_ENABLED needs the published legal documents: {', '.join(missing_legal)}")
        if self.billing_enabled and not self.STRIPE_WEBHOOK_SECRET:
            problems.append("STRIPE_WEBHOOK_SECRET is required with STRIPE_SECRET_KEY")
        if self.NUMBER_PROVISIONING_ENABLED and self.telephony_provider == "twilio":
            if not (self.TWILIO_ACCOUNT_SID and self.TWILIO_AUTH_TOKEN):
                problems.append("NUMBER_PROVISIONING_ENABLED needs TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN")
            if not (self.TWILIO_SIP_TRUNK_SID and self.LIVEKIT_SIP_INBOUND_TRUNK_ID):
                problems.append("NUMBER_PROVISIONING_ENABLED needs TWILIO_SIP_TRUNK_SID and LIVEKIT_SIP_INBOUND_TRUNK_ID")
        if problems:
            raise ValueError("Unsafe production config: " + "; ".join(problems))
        return self


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
