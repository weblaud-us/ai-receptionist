> **Snapshot from before Phase 0** (formerly `BACKEND_MAP.md`). Parts are outdated: Phase 0 added
> webhook signature checks, signed call links, the `room_nights` double-booking guard, `core/`,
> JSON logging and `/livez` + `/readyz`, and removed `/livekit/send-whatsapp`, `webhook.py`,
> `whatsapp/prompts.py` and `utils/logger.py`. It will be rewritten at the end of Phase 2
> (see ROADMAP.md §2.3). For the current state, start with `AI-receptionist-1/README.md`.
> Phase 1 replaced `livekit_agent.py` with the `agent/` package (phone via SIP + browser calls)
> and moved the tools to `core/tools/`.

# Backend Map — AI Hotel Receptionist

Location: `AI-receptionist-1/` (Python 3.12, FastAPI, MongoDB/Motor, OpenAI, ElevenLabs, Twilio, Meta WhatsApp Cloud API, LiveKit).
Mapped from a full read of every `.py` file, config and deploy file. Secret **values** in `.env` are deliberately not reproduced here — only variable names.

> Single-tenant. One hotel, configured entirely from `.env`. `database/tenancy.py` keeps multi-tenant function names as no-op stubs so old imports still work.

---

## 1. Repository layout

```
receptionist/
├── BACKEND_MAP.md                  ← this file
├── dashboard/                      Vite + React + TS frontend (currently makes NO calls to the backend)
└── AI-receptionist-1/              BACKEND
    ├── main.py                     FastAPI app, lifespan, router registration, health/debug routes (304 lines)
    ├── config.py                   pydantic-settings `Settings` → `settings` singleton (119)
    ├── livekit_agent.py            SEPARATE PROCESS: LiveKit voice worker (330)
    ├── webhook.py                  Stand-alone minimal Meta webhook app — NOT used (27)
    ├── ai/
    │   ├── agent.py                HotelAgent: LLM ↔ tools loop, memory, style post-processing (522)
    │   ├── llm_provider.py         OpenAI chat-completions wrapper (118)
    │   ├── prompts.py              System prompt + message templates (ACTIVE) (294)
    │   └── tools.py                7 tool schemas + HotelToolExecutor (Mongo logic) (897)
    ├── database/
    │   ├── mongodb.py              Motor client, indexes (91)
    │   ├── models.py               Pydantic models/enums (289)
    │   └── tenancy.py              Hotel profile from settings + stubs (72)
    ├── routers/
    │   ├── api.py                  /bookings, /rooms REST (272)
    │   └── livekit.py              /call/{room}, /livekit/token, /livekit/send-whatsapp (99)
    ├── voice/
    │   ├── twilio_handler.py       /voice/* — phone calls via Twilio Media Streams (628)
    │   └── stt_tts.py              Whisper STT + ElevenLabs TTS (167)
    ├── whatsapp/
    │   ├── meta_handler.py         /webhook — Meta WhatsApp Cloud API (683)
    │   ├── handler.py              Session get/create + post-call summary (130)
    │   └── prompts.py              Older prompt set — NOT imported anywhere (165)
    ├── services/
    │   ├── reminders.py            1-day-before-check-in WhatsApp reminder loop (190)
    │   └── calendar.py             ICS + Google Calendar link generation (132)
    ├── utils/
    │   ├── seed_rooms.py           Seeds 100 rooms (destructive: wipes `rooms`) (188)
    │   └── logger.py               Rotating-file logging — NOT called anywhere (50)
    ├── ui/call.html                Browser call page (LiveKit JS client) (446)
    ├── temp_audio/                 Empty
    ├── .env / .env.example         Config (`.env` is gitignored)
    ├── requirements.txt, pytest.ini
    ├── start.sh, start.bat, deploy.sh, ai-receptionist.service, .github/workflows/deploy.yml
    └── README.md, DEPLOY_GUIDE.md  (README is partly stale — see §12)
```

---

## 2. Runtime processes

| Process | Command | Port | What it does |
|---|---|---|---|
| **API server** | `uvicorn main:app --host 0.0.0.0 --port 8002` (or `python main.py`) | 8002 (config default; `main.py` docstring says 8000) | All HTTP/WebSocket endpoints, reminder loop, TTS prewarm |
| **LiveKit worker** | `python livekit_agent.py dev` / `start` | `LIVEKIT_WORKER_PORT` (0 = random) | Joins LiveKit rooms as the AI receptionist (browser calls) |

`start.bat` launches both (Windows). `start.sh` launches only the API (and optionally seeds rooms if `SEED_ROOMS_ON_START=true`). systemd unit runs the API with `--workers 1` — this **must** stay at 1 worker because call sessions and webhook dedup live in process memory.

### Startup sequence (`main.py` lifespan)
1. Console → UTF-8, logging to stdout + `hotel_ai.log` (cwd).
2. Routers imported inside try/except — a failing import logs an error and that router is skipped (app still boots).
3. `connect_db()` → Motor client (`maxPoolSize=50`, `minPoolSize=5`, 5 s server-selection timeout) → create all indexes.
4. `get_llm_provider()` → builds `OpenAIProvider` (raises if `OPENAI_API_KEY` empty).
5. `BookingReminderService.start()` → background asyncio task.
6. Schedules `_ensure_audio_cache(get_tts())` — pre-synthesises the voice greeting + 4 filler phrases.
7. Shutdown: stop reminder task, close Mongo.

Middleware: CORS `allow_origins=["*"]`, `allow_credentials=True`, all methods/headers. Global exception handler returns `{"error":"Internal server error","detail": <str(exc) if DEBUG else "Contact support">}`.

---

## 3. HTTP / WebSocket endpoint inventory

### Health & debug (`main.py`)
| Method | Path | Notes |
|---|---|---|
| GET | `/` | Service name, status, version `1.0.0`, env, LLM, docs link |
| GET | `/health` | Pings Mongo → `{status, database: connected\|disconnected, hotel}` (always `status:"healthy"`, even if DB down) |
| GET | `/debug/routes` | Lists all registered routes |
| GET | `/debug/rooms` | Room counts by type + 5 samples |
| GET | `/debug/bookings` | First 10 bookings (id, **guest name**, type, dates, status) |
| GET | `/docs`, `/redoc` | Swagger / ReDoc |

### Bookings (`routers/api.py`, prefix `/bookings`)
| Method | Path | Behaviour |
|---|---|---|
| GET | `/bookings/lookup/search?phone=&booking_id=&guest_name=` | Priority: booking_id > phone > guest_name. Phone matches `+X`, `X`, and a **case-insensitive regex** on the digits. Name is a raw `$regex`. Max 10, newest first. Returns trimmed fields incl. `num_guests`, `pricing`. |
| GET | `/bookings/` | Filters `status`, `check_in_date`, `guest_phone`; `page` (≥1), `limit` (≤100, default 20). Returns `{bookings,total,page,pages}`. |
| GET | `/bookings/{booking_id}` | Full doc (upper-cased id), 404 if missing. |
| GET | `/bookings/{booking_id}/calendar.ics?token=` | Returns `.ics` if `token == booking.calendar_token`, else 403. `Cache-Control: private, max-age=3600`. |
| POST | `/bookings/` | Body `BookingCreateRequest`. Looks up room by `_id` only to read its **type**, then calls `executor.create_booking` (which picks *any* available room of that type — see §12). 400 on failure. |
| PATCH | `/bookings/{booking_id}/status?status=` | Status must be a `BookingStatus` value. Raw `$set`, no transition rules, no side-effects. |
| DELETE | `/bookings/{booking_id}?reason=` | → `executor.cancel_booking` (applies fee policy). |

### Rooms (`routers/api.py`, prefix `/rooms`)
| Method | Path | Behaviour |
|---|---|---|
| GET | `/rooms/?room_type=&is_active=true` | Sorted by room_number, max 200. |
| POST | `/rooms/` | Body `Room`. 400 if `room_number` exists. |
| GET | `/rooms/availability?check_in_date=&check_out_date=&room_type=&adults=&children=` | → `executor.check_room_availability` (includes alternatives). |

### Voice — Twilio (`voice/twilio_handler.py`, prefix `/voice`)
| Method | Path | Behaviour |
|---|---|---|
| GET | `/voice/incoming` | Plain-text fallback. |
| POST | `/voice/incoming` | Twilio webhook. Idempotent (retries return same TwiML). Creates in-memory session + `call_sessions` doc + `conversations` doc (with memory). Returns TwiML `<Connect><Stream url=wss://…/voice/stream track=inbound_track>`. On any error still returns the TwiML. |
| POST | `/voice/status` | Twilio status callback. On `completed/failed/busy/no-answer`: pop session, mark conversation `completed` (+`duration_sec`,`ended_at`), delete `call_sessions` doc; if `completed` → fire-and-forget WhatsApp booking summary. |
| WS | `/voice/stream` | Twilio Media Stream (see §5.1). |
| POST | `/voice/outbound` | Body `{to_phone, message}`. Header `X-Admin-Key` checked **only if** `API_KEY_REQUIRED=true`. Places a call that just `<Say>`s the message (Polly.Joanna) — not an interactive AI call. |

### WhatsApp — Meta (`whatsapp/meta_handler.py`, no prefix)
| Method | Path | Behaviour |
|---|---|---|
| GET | `/webhook` | Meta verification: `hub.mode=subscribe` and `hub.verify_token == META_WA_VERIFY_TOKEN` → echo challenge, else 403. |
| POST | `/webhook` | Inbound events; always returns `ok` (errors are logged, not surfaced). See §5.2. |

### LiveKit (`routers/livekit.py`)
| Method | Path | Behaviour |
|---|---|---|
| GET | `/call/{room_id}` | Serves `ui/call.html`. |
| GET | `/livekit/token?room=&identity=` | Needs `LIVEKIT_URL/API_KEY/API_SECRET`. Issues JWT (`room_join`, that room). Returns `{token,url,room,identity,call_url,hotel_name,receptionist_name,hotel_phone}`. `room` min length 3; identity defaults `guest_<8 hex>`. |
| POST | `/livekit/send-whatsapp` | Body `{to,message}` → sends via Meta; 502 on failure. |

**Total: 25 application routes** (5 health/debug, 7 bookings, 3 rooms, 5 voice, 2 WhatsApp, 3 LiveKit) plus the built-in `/docs`, `/redoc`, `/openapi.json`. None except `/voice/outbound` has any authentication.

---

## 4. AI core

### 4.1 `HotelAgent` (`ai/agent.py`)
One class, two channels (`voice`, `whatsapp`) via `get_voice_agent()` / `get_whatsapp_agent()`.

`process_message(session_id, user_message, phone)`:
1. Load conversation doc (create if missing).
2. `detect_gender_preference()` on the message → persists `context.guest_gender` (drives "sir"/"ma'am").
3. `_build_messages()`:
   - system prompt (`ai/prompts.get_system_prompt`) with hotel profile + context;
   - second system message `CALLER PHONE: …` telling the LLM to call `get_booking_details` with it;
   - history: voice last 6 turns (stale greeting stripped), WhatsApp last 8;
   - voice only: `CALL STATUS: ACTIVE… do not re-greet` message, plus `VOICE_BREVITY_REMINDER` every 4th history length;
   - the user message.
4. Tool loop, max **5 iterations**: `chat_completion(tools=HOTEL_TOOLS, tool_choice="auto", temperature=0.7, max_tokens=150 voice / 500 WhatsApp)`. If no native tool calls, tries a text fallback parser for `<|tool_call_start|>…<|tool_call_end|>`.
5. `_enforce_style()`: strips the words AI/artificial intelligence/chatbot/etc., strips re-greeting openers (voice), strips robotic scope-refusals, collapses whitespace.
6. `_save_turn()`: pushes user + assistant messages (with tool_calls) to `conversations`; upserts `guests` (`last_interaction`).
7. `_update_context()`: on successful `create_booking` → `context.booking_id`, stage `booked`, `$addToSet booking_ids`; on `check_room_availability` → stores dates, stage `collecting_info`.

`create_conversation_with_memory()` — creates a new conversation per call/session, seeded from the same phone's most recent conversation within `CONVERSATION_MEMORY_DAYS` (15): last `CONVERSATION_MEMORY_MAX_MESSAGES` (20) messages for WhatsApp, last 8 non-greeting messages for voice; copies prior context (dates, room type, adults/children, intent, etc.) but resets `booking_id` and stage.

Note: `OPENAI_TEMPERATURE` from settings is **not used** — the agent hard-codes 0.7.

### 4.2 `OpenAIProvider` (`ai/llm_provider.py`)
`AsyncOpenAI` chat completions; model = `OPENAI_MODEL` (default `gpt-4o-mini`; `.env.example` says `gpt-4o`). Returns `{content, tool_calls, usage}`. Also has `stream_completion` (unused). `LLM_PROVIDER` is read but only OpenAI exists.

### 4.3 Tools (`ai/tools.py`) — the LLM's only way to touch data
| Tool | Required args | What it does |
|---|---|---|
| `check_room_availability` | check_in_date, check_out_date (opt: room_type, adults, children) | Validates (out>in, in not in past). Active, non-maintenance rooms with `capacity ≥ adults+children`. Excludes rooms with `confirmed/pending/checked_in` bookings overlapping (same-day checkout/check-in allowed). Returns rooms with `price_per_night`, `total_price` (= base×nights×(1+tax)), amenities. If none: up to 3 **alternative room types** (priority-ordered, 1 room each) and up to 3 **alternative date ranges** (scans next 14 days, same length). |
| `get_room_pricing` | room_type, check_in_date, check_out_date | Uses the first active room of the type. Applies weekly (≥7 nights) or monthly (≥30) discount %, then tax. Returns base_total, discount, tax (key named `tax_15pct`), final_total, amenities. |
| `create_booking` | guest_name, guest_phone, room_type, dates | Re-checks availability, takes the **first** available room, prices it, upserts guest, inserts booking (`status=confirmed`, `source=ai_receptionist`, `calendar_token`, reminder flags), increments `total_stays`, builds ICS + Google Calendar links. |
| `get_booking_details` | booking_id **or** guest_phone | Phone normalised to variants (`+880…`, `880…`, `01…`). Max 5 newest. |
| `cancel_booking` | booking_id (opt reason) | Rejects `checked_out/cancelled`. Fee: same day or past = 100 %, <3 days = 50 %, else free. Sets `cancelled`, appends a `booking_notes` entry. Fee is **reported only** — nothing is charged/refunded. |
| `get_hotel_info` | info_type: general/amenities/policies/location/rooms_overview | **Hard-coded** facts (pool, spa, restaurants, landmarks near Bashundhara/Gulshan, "5 stars", pet/smoking/child policy). Only name/address/phone/times come from settings. |
| `send_booking_confirmation_whatsapp` | booking_id, whatsapp_number | Sends a text confirmation via Meta Graph API; sets `whatsapp_confirmation_sent(_at)` on the booking. |

Helper: `_get_guest_bookings_by_phone()` (voice greeting context; currently unused by `twilio_handler`).

---

## 5. Channel flows

### 5.1 Phone call (Twilio) — `voice/twilio_handler.py`
```
Caller ─► Twilio ─► POST /voice/incoming ─► (session + call_sessions + conversation) ─► TwiML <Stream>
                  └► WS /voice/stream
   event start  → attach websocket, play cached greeting (GREETING_VOICE), persist greeting to conversation
   event media  → ignored while ai_speaking / busy / greeting not yet sent
                  VAD state machine IDLE → SPEAKING → TRAILING
                     SPEECH_THRESHOLD RMS 200 · end after 60×20 ms (1.2 s) silence
                     min 15 chunks (300 ms) · hard cap 1500 chunks (30 s) · idle hang-up 50 s
                  utterance → mulaw→WAV → Whisper (language="en")
                            → filler audio plays immediately ("One moment, please." etc.)
                              while HotelAgent.process_message runs in parallel
                            → ElevenLabs TTS (eleven_turbo_v2, native ulaw_8000) → 20 ms frames to Twilio
   event stop   → flush last utterance, mark conversation completed
   POST /voice/status (completed) → WhatsApp booking summary if the call produced bookings
```
Limits worth knowing: no barge-in (caller audio is dropped while the AI speaks); STT is forced to English; `audioop` is used (removed in Python 3.13 → needs ≤3.12); the Twilio signature check flag is never read.

### 5.2 WhatsApp (Meta Cloud API) — `whatsapp/meta_handler.py`
```
POST /webhook → for each entry/change/message:
   dedup by message id (in-memory OrderedDict, 10 min TTL, 5000 max)
   text  → body            audio → download media → Whisper → body
   audio w/ empty transcript → "couldn't understand the voice note"
   ┌ call-request keywords (EN + Bangla)  → reply with Twilio number + browser link /call/wa_<phone>_<ts>   (no agent)
   ├ session = get-or-create active WhatsApp conversation (same phone, active, within memory window)
   ├ quick greeting ("hi", "how are you", "good morning"…) → canned greeting (full welcome for new guest,
   │                                                         varied one for returning)             (no agent)
   ├ off-topic (no hotel keyword) → canned redirect, or "my name is Aria…" if asked name           (no agent)
   └ otherwise → HotelAgent.process_message
         if create_booking succeeded → send rich confirmation template (ICS + Google Calendar links) and STOP
         elif the inbound was audio  → TTS (ElevenLabs, fallback OpenAI) → upload media → send audio reply
         else send text (split into ≤1500-char chunks)
   any exception → apology message
```
Quirks: voice-note TTS uses ElevenLabs `ulaw_8000` bytes uploaded as `audio/mpeg` (format mismatch — unverified whether WhatsApp plays it); the off-topic filter is a keyword allow-list, so a hotel question with none of the keywords is blocked.

### 5.3 Browser call (LiveKit)
`WhatsApp link → GET /call/{room} (call.html) → GET /livekit/token → browser joins room → livekit_agent worker joins same room`.

`livekit_agent.py`: `HotelReceptionistAgent` exposes the same 7 tools via `@function_tool`; `on_enter()` plays the greeting once. Default **pipeline** session: Silero VAD + Whisper + `OPENAI_MODEL` + **OpenAI TTS** (not ElevenLabs). Endpointing 0.3–3.0 s, interruptions allowed. It does **not** use `HotelAgent`, conversation memory, caller-phone injection or conversation persistence. Realtime mode exists but cannot be switched on (§12 #1).

### 5.4 Reminders — `services/reminders.py`
Background loop every `BOOKING_REMINDER_POLL_MINUTES` (30). If `ENABLE_ONE_DAY_BOOKING_REMINDER` and `ENABLE_WHATSAPP`: finds `status=confirmed`, `check_in_date == tomorrow (hotel timezone)`, `one_day_reminder_sent` false/missing (max 200 per run); sends a WhatsApp text with Google/ICS links; records `one_day_reminder_sent(_at)`, `one_day_reminder_attempted_at`, or `one_day_reminder_error="send_failed"`. Failed ones are retried every poll (no backoff/limit).

### 5.5 Calendar — `services/calendar.py`
`generate_calendar_token` = first 24 hex chars of `sha256(booking_id:phone:SECRET_KEY)`. ICS URL = `{WEBHOOK_BASE_URL or CALL_BASE_URL}/bookings/{id}/calendar.ics?token=…&hotel_id=default` (empty if no base URL). ICS has a 1-day `VALARM` and `TZID` = hotel timezone.

---

## 6. MongoDB

DB `MONGODB_DB_NAME` (default `hotel_ai_dev`). Indexes are created at every startup.

| Collection | Indexes | Purpose |
|---|---|---|
| `rooms` | `room_number` **unique**; `room_type`; `is_active` | Inventory (`_id` is a UUID **string**) |
| `bookings` | `booking_id` **unique**; `guest_phone`; `room_id`; `(check_in_date, check_out_date)`; `(check_in_date, status, one_day_reminder_sent)`; `status`; `created_at` desc | Reservations |
| `guests` | `phone` **unique**; `email`; `name` | Guest profiles |
| `conversations` | `session_id` **unique**; `phone`; `channel`; `created_at` desc; `status` | Chat/call transcripts + context |
| `call_sessions` | `call_sid` **unique**; `phone`; `created_at` desc | Live Twilio call state (deleted on call end) |

### Document shapes
**Room**: `_id`, `room_number`, `room_type` (standard/deluxe/suite/executive/presidential), `bed_type` (single/double/queen/king/twin), `floor`, `capacity`, `size_sqft`, `description`, `amenities{wifi,ac,tv,minibar,balcony,sea_view,jacuzzi,kitchen,workspace,breakfast_included}`, `pricing{base_price_per_night, weekend_surcharge_pct, peak_season_surcharge_pct, discount_weekly_pct, discount_monthly_pct, tax_pct=15}`, `images[]`, `is_active`, `is_maintenance`, `created_at`, `updated_at`.

**Booking**: `_id`, `booking_id` (`HTL` + 8 hex), `guest_phone/name/email`, `room_id`, `room_number`, `room_type`, `check_in_date`/`check_out_date` (**stored as `YYYY-MM-DD` strings**), `adults`, `children`, `special_requests`, `pricing{base_total,taxes,discounts,final_total,per_night_avg,nights,currency}`, `status` (pending/confirmed/checked_in/checked_out/cancelled/no_show), `payment_status`, `source`, `booking_notes[]`, `confirmation_sent`, `calendar_token`, `one_day_reminder_{sent,sent_at,attempted_at,error}`, timestamps, `checked_in_at`, `checked_out_at`. Added ad-hoc by code: `whatsapp_confirmation_sent`, `whatsapp_confirmation_sent_at`.

**Guest**: `_id`, `phone`, `name`, `email`, `nationality`, `id_number`, `preferences{}`, `total_stays`, `vip_tier`, `created_at/updated_at`, `last_interaction`.

**Conversation**: `_id`, `session_id`, `phone`, `channel` (voice/whatsapp), `call_sid`, `messages[{role,content,timestamp,tool_calls,tool_results,audio_duration_sec}]`, `context{guest_name, guest_phone, check_in_date, check_out_date, room_type, adults, children, special_requests, intent, booking_id, conversation_stage, previous_bookings[]}`, `status` (active/completed/abandoned), `booking_ids[]`, `duration_sec`, timestamps, `ended_at`. Ad-hoc: `context.guest_gender`, `summary_sent`, `summary_sent_at`.

**CallSession**: `call_sid`, `phone`, `session_id`, `websocket_connected`, `audio_buffer`, `partial_transcript`, `is_speaking`, `silence_counter`, `created_at`, `last_activity` (only the identifying fields are actually used).

API-only models: `AvailabilityRequest/Response`, `BookingCreateRequest`, `BookingResponse` (only `BookingCreateRequest` is used).

**Seed** (`python -m utils.seed_rooms`): deletes **all** rooms, inserts 100 (20 per type):

| Type | Rooms | Floors | Cap | Bed | BDT/night |
|---|---|---|---|---|---|
| standard | 101–120 | 1–2 | 2 | double | 4,500 |
| deluxe | 301–320 | 3–4 | 2 | queen | 8,500 |
| suite | 501–520 | 5–6 | 3 | king | 15,000 |
| executive | 701–720 | 7–8 | 2 | king | 12,000 |
| presidential | 901–920 | 9–10 | 4 | king | 25,000 |

---

## 7. Configuration (`config.py` → `.env`)

"Used?" was checked by searching for each setting across all `.py` files.

| Group | Variable (default) | Used? |
|---|---|---|
| App | `APP_NAME`, `APP_ENV` (development), `DEBUG` (true), `HOST` (0.0.0.0), `PORT` (8002) | yes (`APP_NAME` only in settings) |
| | `WEBHOOK_BASE_URL`, `WEBHOOK_TIMEOUT_SECONDS` (30) | base URL yes; **timeout no** |
| Mongo | `MONGODB_URI`, `MONGODB_DB_NAME` | yes |
| LLM | `LLM_PROVIDER` (openai) | **no** |
| | `OPENAI_API_KEY`, `OPENAI_MODEL` (gpt-4o-mini) | yes |
| | `OPENAI_TEMPERATURE` (0.6) | **no** (agent hard-codes 0.7) |
| | `OPENAI_VOICE_MODEL`, `OPENAI_REALTIME_VOICE` | LiveKit realtime only (unreachable) |
| | `OPENAI_TTS_MODEL`, `OPENAI_TTS_VOICE` | LiveKit pipeline + WhatsApp TTS fallback |
| STT/TTS | `STT_PROVIDER` (openai_whisper), `TTS_PROVIDER` (elevenlabs) | only inside `meta_handler` |
| | `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` | yes |
| Twilio | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER` | yes |
| | `TWILIO_WHATSAPP_NUMBER`, `VERIFY_TWILIO_SIGNATURE`, `WHATSAPP_MAX_PARTS` | **no** |
| Meta WA | `META_WA_VERIFY_TOKEN`, `META_WA_ACCESS_TOKEN`, `META_WA_PHONE_NUMBER_ID`, `META_WABA_ID` (unused), `META_GRAPH_API_VERSION` (v20.0) | yes |
| LiveKit | `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `CALL_BASE_URL`, `LIVEKIT_WORKER_PORT` (0) | yes |
| Memory | `CONVERSATION_MEMORY_DAYS` (15), `CONVERSATION_MEMORY_MAX_MESSAGES` (20) | yes |
| Hotel | `HOTEL_NAME` (Grand Azure Hotel), `RECEPTIONIST_NAME` (Aria), `HOTEL_PHONE`, `HOTEL_ADDRESS`, `HOTEL_CHECKIN_TIME` (14:00), `HOTEL_CHECKOUT_TIME` (12:00), `HOTEL_CURRENCY` (BDT), `HOTEL_TIMEZONE` (Asia/Dhaka) | yes |
| Security | `SECRET_KEY` (change-in-production), `ADMIN_SECRET_KEY` (change-in-production), `API_KEY_REQUIRED` (false) | yes (calendar token / `/voice/outbound`) |
| | `WEBHOOK_SECRET` | **no** |
| Logging | `LOG_LEVEL` | yes |
| | `LOG_FILE`, `LOG_MAX_SIZE_MB`, `LOG_BACKUP_COUNT` | only by the unused `utils/logger.py` |
| Flags | `ENABLE_ONE_DAY_BOOKING_REMINDER`, `ENABLE_WHATSAPP`, `BOOKING_REMINDER_POLL_MINUTES` | yes (reminders) |
| | `ENABLE_VOICE_CALLS`, `ENABLE_BOOKING_CONFIRMATION_SMS`, `ENABLE_CALL_RECORDING` | **no** |

`Settings` has `extra="ignore"`, so any `.env` key not declared above is silently dropped.

`.env` also contains names that nothing reads (leftovers from earlier versions): `GOOGLE_SERVICE_ACCOUNT_JSON`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `DEEPGRAM_API_KEY`, `GOOGLE_CLOUD_CREDENTIALS`, `GOOGLE_TTS_LANGUAGE`, `VOICE_NAME`, and a duplicated `RECEPTIONIST_NAME`.

---

## 8. External services

| Service | Used for | Where |
|---|---|---|
| OpenAI chat | Agent LLM | `llm_provider.py` |
| OpenAI Whisper (`whisper-1`) | Phone STT, WhatsApp voice-note STT, LiveKit STT | `stt_tts.py`, `meta_handler.py`, `livekit_agent.py` |
| OpenAI TTS | LiveKit voice; WhatsApp voice-note fallback | `livekit_agent.py`, `meta_handler.py` |
| OpenAI Realtime | Optional LiveKit mode | `livekit_agent.py` (unreachable) |
| ElevenLabs (`eleven_turbo_v2`, `ulaw_8000`) | Phone TTS, filler/greeting cache, WhatsApp voice replies | `stt_tts.py` |
| Twilio Voice | Inbound calls + outbound `<Say>` calls | `twilio_handler.py` |
| Meta Graph API (`v20.0`) | WhatsApp text/audio send, media download/upload, webhook | `meta_handler.py`, `tools.py`, `reminders.py` |
| LiveKit | Browser calls (token + agent worker) | `routers/livekit.py`, `livekit_agent.py` |
| MongoDB (Atlas/local) | All persistence | `database/` |
| Google Calendar | URL template only (no API) | `calendar.py` |

Meta send logic is **duplicated three times** (`meta_handler._send_meta_whatsapp`, `reminders._send_meta_whatsapp`, and inline in `tools.send_booking_confirmation_whatsapp`).

---

## 9. Dependencies (`requirements.txt`)
fastapi≥0.130, uvicorn[standard], python-multipart, pydantic 2.9.2, pydantic-settings 2.6.0, python-dotenv, protobuf 5.x · motor 3.6.0 / pymongo 4.9.2 · openai ≥1.109.1 · elevenlabs ≥2.34 · twilio 9.3.7 · httpx 0.27.2, aiofiles · livekit-api, livekit-agents, livekit-plugins-{openai,elevenlabs,silero} · python-dateutil, pytz · pytest 7.4, pytest-asyncio, anyio. OS-level `ffmpeg` is listed in comments but the code no longer needs it.

---

## 10. Deployment & ops

- **systemd** (`ai-receptionist.service`): user `ubuntu`, dir `/home/ubuntu/AI-receptionist`, `EnvironmentFile=.env`, 1 worker, port 8002, `Restart=always`. The LiveKit worker has **no** unit/process manager defined.
- **CI** (`.github/workflows/deploy.yml`): on push to `main` → SSH to VPS → `git pull` → `pip install -r requirements.txt` → `pm2 restart ecosystem.config.js` → `pm2 status`. Secrets: `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_PORT`, `APP_DIR`.
- **`deploy.sh`**: same steps manually; prints `https://hotel.weblaud.com`.
- **DEPLOY_GUIDE.md** (Bengali) documents systemd + Nginx (WebSocket upgrade headers, 300 s read timeout) and log commands (`journalctl -u ai-receptionist`).
- Git: `AI-receptionist-1/` is its own git repo (the parent `receptionist/` folder is not). `DEPLOY_GUIDE.md` names the GitHub repo `ruhul-cse-duet/Hotel-AI-Receptionist-Agent-voice-and-message`.

---

## 11. Frontend relationship
`dashboard/` (Vite/React/TS) contains **no** `fetch`/axios/env-URL usage today, so it isn't wired to any endpoint above. The only HTML the backend serves itself is `ui/call.html`, which calls `/livekit/token` and loads `livekit-client` from jsDelivr.

---

## 12. Issues found while mapping (verified in code)

**Bugs / functional gaps**
1. **LiveKit Realtime mode can't be enabled.** `livekit_agent.py` reads `getattr(settings, "LIVEKIT_USE_REALTIME", "false")`, but `Settings` doesn't declare that field and `extra="ignore"` drops it → always pipeline mode. Fix: add `LIVEKIT_USE_REALTIME: bool = False` to `Settings`.
2. **`POST /bookings/` ignores the chosen `room_id`.** It only reads the room's type; `create_booking` then assigns the first available room of that type, which may be a different room.
3. **Price inconsistency.** `check_room_availability.total_price` ignores weekly/monthly discounts; `get_room_pricing` applies them. Weekend/peak surcharges in the schema are never applied anywhere. Booking-time price comes from the *first* room of the type, not the booked room. The pricing key `tax_15pct` is hard-coded in name though tax is per-room.
4. **Deploy references a missing file.** `deploy.sh` and `deploy.yml` run `pm2 restart ecosystem.config.js`, but no `ecosystem.config.js` exists, and the documented runner is systemd. As written, CI deploy will fail.
5. **`pytest.ini` points at `tests/`, which doesn't exist** — no tests in the repo.
6. **`/health` always reports `"status":"healthy"`** even when `database` is `disconnected`.
7. **`total_stays` increments at booking time** (and never decrements on cancel), not at check-out.
8. **Cancellation fee is only returned, never enforced**; `PATCH /status` can set any status with no transition rules or availability effects.
9. **Booking status lookups mix formats:** `check_in_date` is a string in Mongo but typed `date` in the model; comparisons rely on ISO string ordering (works, but fragile).
10. **LiveKit agent log line is wrong** (`"Pipeline (Whisper -> GPT-4o -> ElevenLabs)"` — it uses OpenAI TTS), and the agent bypasses memory/persistence entirely, so browser calls leave no transcript and no WhatsApp summary.
11. **WhatsApp voice-note replies** upload ElevenLabs `ulaw_8000` data labelled `audio/mpeg` — likely not playable; untested.
12. **Reminders** retry failed sends forever every 30 min with no cap.

**Security**
13. **No authentication** on `/bookings*`, `/rooms*` (including `POST /rooms`, `PATCH`/`DELETE` bookings) or `/debug/*`. `/debug/bookings` and `/bookings/` expose guest names, phones and prices to anyone who can reach the server. `API_KEY_REQUIRED` only guards `/voice/outbound`.
14. **Unescaped user input in `$regex`** (`/bookings/lookup/search` name/phone) — regex injection / possible slow patterns.
15. **Webhook authenticity not verified:** `VERIFY_TWILIO_SIGNATURE` and `WEBHOOK_SECRET` are never read; no `X-Hub-Signature-256` check on Meta's `/webhook`; no Twilio signature check on `/voice/*`.
16. **CORS `*` with `allow_credentials=True`.**
17. **Agent tools have no caller verification:** anyone chatting can ask to cancel/look up any booking by ID or phone. `cancel_booking` doesn't check that the caller owns the booking.
18. **Weak defaults:** `SECRET_KEY` and `ADMIN_SECRET_KEY` default to `change-in-production`; the calendar token (and so the ICS link) is derived from `SECRET_KEY`. `DEBUG` defaults to `true`, which makes the global error handler return exception text.
19. `.env` contains live-looking credentials on disk (gitignored, so not committed — but rotate them if this folder was ever shared or zipped).

**Dead / stale code**
20. Unused: `webhook.py`, `whatsapp/prompts.py` (duplicate prompt set), `utils/logger.py`, `BaseLLMProvider.stream_completion`, `synthesize_stream`, `BaseTTS.prewarm`, `_get_guest_bookings_by_phone`, models `AvailabilityResponse/BookingResponse/CallSession` extra fields, 10 declared-but-unread settings (§7), `ToolExecutor` alias.
21. **README is out of date**: describes Twilio WhatsApp, Gemini, `/admin/ws`, `knowledge_items` and `agent_settings` collections — none exist in the code.
22. `main.py` docstring says port 8000, startup banner says "TTS: ElevenLabs / OpenAI", description says GPT-4o; actual defaults are 8002, ElevenLabs, `gpt-4o-mini`.

**Operational constraints**
23. In-memory state (`active_sessions`, Meta dedup cache, TTS audio cache) → **single worker only**; a restart drops live calls' session map.
24. `audioop` needs Python ≤ 3.12.
25. `hotel_ai.log` is written to the working directory with no rotation (the rotating logger isn't wired).

---

## 13. Quick reference — data flow for one booking
```
Guest message/voice ─► channel handler (Twilio WS | Meta webhook | LiveKit)
   ─► HotelAgent.process_message (voice/WhatsApp) ─► OpenAI ─► tool_calls
        check_room_availability ─► rooms + bookings
        create_booking ─► guests (upsert) + bookings (insert) + calendar links
   ─► conversations (messages, context, booking_ids)
   ─► reply: TTS audio (call) | WhatsApp text + rich confirmation
 later:  call ends ─► /voice/status ─► WhatsApp summary
         day before check-in ─► reminder loop ─► WhatsApp reminder
```
