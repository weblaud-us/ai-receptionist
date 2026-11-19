# AI Receptionist — backend

FastAPI API + LiveKit voice agent for many local businesses — salons, home services, restaurants,
clinics, offices and hotels — each with its own number, hours, services, knowledge and voice
(Phase 2, see [../ROADMAP.md](../ROADMAP.md) and [../docs/PHASE_2_TESTING.md](../docs/PHASE_2_TESTING.md)).
This README describes what exists **today**.

## What it does

| Channel | Path | How |
|---|---|---|
| Phone calls | Twilio number → Elastic SIP trunk → LiveKit → `agent/worker.py` | streaming Deepgram STT → `VOICE_LLM_MODEL` → ElevenLabs Flash; turn detector, barge-in, transfer, messages ([telephony/README.md](telephony/README.md)) |
| Browser calls | WhatsApp "call me" → signed link `/call/{room}?exp&sig` → LiveKit room | same agent (`agent/worker.py`) |
| Phone calls (legacy) | Twilio → `POST /voice/incoming` → media-stream WebSocket `/voice/stream` | batch Whisper → GPT → ElevenLabs. Kept for one release behind `LEGACY_TWILIO_VOICE`; deleted at cutover (Roadmap Task 1.13) |
| WhatsApp | Meta Cloud API → `POST /webhook` | text and voice notes; replies by text or audio |
| Dashboard API | `/admin/*`, `/bookings/*`, `/rooms/*` | JWT login (`ADMIN_USERNAME`/`ADMIN_PASSWORD`) or `X-Admin-Key` |
| Reminders | background task in the API | WhatsApp reminder one day before check-in |

The AI can check availability, quote prices, book, look up and cancel bookings, send a
WhatsApp confirmation, take a message for the team, and (on phone calls) transfer to a person
and hang up. It always introduces itself as a **virtual assistant** and answers honestly if
asked whether it is an AI. Every tool is implemented once (`core/tools/`) and shared by voice
and WhatsApp.

## Multi-tenant core (Phase 2)

- **Tenants**: every business is a `tenants` document (`core/tenancy`). Inbound calls resolve the
  tenant from the dialled number (`phone_numbers`), WhatsApp from the receiving number id, the
  dashboard from the signed-in user. Unknown numbers get a neutral message, never a crash.
- **Isolation**: tenant data is read and written through `core/repositories` (tenant_id on every
  query); older routers use `scoped_db`. `tests/test_api_tenancy.py` proves no endpoint or tool
  leaks across tenants; `tests/test_isolation_guard.py` keeps new code on repositories.
- **Any business**: a prompt builder with vertical templates (`core/prompts`), generic tools
  (availability, booking, rescheduling, cancelling, leads, knowledge, messages) and a DST-safe
  availability engine. `slot_locks` (unique per resource per 5 minutes) makes double booking
  impossible. Hotels keep their room tools as a plug-in.
- **Dashboard**: real users (argon2id), roles (owner / admin / staff), rotating refresh cookie,
  TOTP two-factor (required for platform admins), onboarding wizard, editable settings, services
  and staff, knowledge editor with website import and unanswered questions, appointments,
  contacts, job requests, call transcripts and recordings, team invites, platform admin.
- **Integrations**: Google Calendar two-way sync, owner notifications by SMS / e-mail / WhatsApp,
  appointment reminders and a daily digest (`services/tenant_jobs.py`).

## Voice agent (Phase 1)

- **One worker for phone and browser calls**: `agent/worker.py`, one job per LiveKit room.
- **Speed**: streaming STT/LLM/TTS, preemptive generation, providers pre-warmed, greeting played
  from a pre-rendered cache, caller history loaded while the greeting plays, nothing written to
  the database between "caller stops" and "agent speaks", every tool ≤ 2 round trips with a
  hard timeout.
- **Feels human**: LiveKit turn detector, interruptions with false-interruption resume, soft
  typing sound only while a tool runs, "are you still there?" on silence.
- **Measured**: every turn's latency is stored on its `calls` document;
  `uv run python -m scripts.latency_report` compares the last calls with the Phase 1 targets
  (p50 ≤ 1.0 s, p95 ≤ 1.8 s, tool turns ≤ 1.6 s, greeting ≤ 0.7 s). The dashboard's Calls page
  shows the same per call, plus messages callers left.
- **Evals**: `tests/evals/` runs scripted conversations against the real LLM — use it to pick
  a model (`VOICE_LLM_MODEL`) and before each release.

## Security model (Phase 0)

- Twilio webhooks need a valid `X-Twilio-Signature`; Meta webhooks need a valid
  `X-Hub-Signature-256` (`META_APP_SECRET`). Forged requests get 403.
- `/livekit/token` only works with a signed link that expires after 15 minutes.
- Booking tools only return or change a booking when the caller's phone matches it, or the
  caller gives the booking ID **and** the full name (or check-in date). Otherwise: `not_found`.
- Double booking is impossible: each booked room-night is a row in `room_nights` with a unique
  `(room_id, night)` index — the database refuses the second claim.
- "Today" is the hotel's date (`HOTEL_TIMEZONE`), never the server's.
- Logs are JSON on stdout with phone numbers and e-mails masked; transcripts only at DEBUG.
- Production refuses to boot with unsafe config (see `config.py → _production_guard`).

## Run locally

Needs [uv](https://docs.astral.sh/uv/) and a MongoDB (Atlas dev database or local).

```bash
cd AI-receptionist-1
cp .env.example .env              # fill in dev values; APP_ENV=development, LOG_FORMAT=text
uv sync                           # installs Python 3.12 + exact versions from uv.lock
uv run python -m utils.seed_rooms # dev DB only — deletes and recreates rooms
uv run python -m migrations.m0002_indexes        # create indexes (also runs on every deploy)
uv run uvicorn main:app --reload --port 8002      # API, docs at http://localhost:8002/docs
uv run python -m agent.worker dev                 # voice agent (separate terminal; Linux/macOS)
```

**Windows:** `livekit-agents` can't be imported on some Windows builds (its native libraries
crash the interpreter), so run the voice worker in Docker — `start.bat` does both:

```bash
docker compose -f docker-compose.dev.yml up --build agent        # voice worker, reloads on edits
docker compose -f docker-compose.dev.yml run --rm tests          # full suite on Linux
```

A MongoDB on your machine is `host.docker.internal` from inside the container. To test a call
without a phone number: send the WhatsApp "call me" keyword and open the link. To test the SIP path
for free (no Twilio), run `uv run python -m scripts.setup_telephony` and call from Linphone: with
`APP_ENV=development` the provider is a softphone, with `production` it's Twilio. Full steps:
[docs/TELEPHONY_TESTING.md](../docs/TELEPHONY_TESTING.md).

For webhooks from Twilio/Meta to reach your machine, expose port 8002 with a tunnel (ngrok,
cloudflared) and set `WEBHOOK_BASE_URL` to the tunnel's https URL:
Twilio voice → `{WEBHOOK_BASE_URL}/voice/incoming`, Meta WhatsApp → `{WEBHOOK_BASE_URL}/webhook`.


## Test, lint, type-check

```bash
uv run pytest          # in-memory MongoDB; no network, no real credentials
uv run ruff check .
uv run mypy            # core/ and agent/ for now

RUN_EVALS=1 uv run pytest tests/evals    # real LLM, a few cents (OPENAI_API_KEY from .env)
```

On Windows the LiveKit-dependent tests skip themselves; CI (Linux) and the Docker `tests`
service run everything.

CI (`.github/workflows/ci.yml` at the repo root) runs all three plus the dashboard build on
every PR (plus dependency audit, secret scan and image builds). Releases are git tags: `v*-rc.*` deploys to
staging, `v*` to staging then production ([deploy/README.md](../deploy/README.md)). Phase 3 details and
acceptance checks: [docs/PHASE_3_TESTING.md](../docs/PHASE_3_TESTING.md); the outbound sales dialer (Phase 4,
off by default): [docs/PHASE_4_TESTING.md](../docs/PHASE_4_TESTING.md); billing, self-serve signup and number
provisioning (Phase 5, off by default): [docs/PHASE_5_TESTING.md](../docs/PHASE_5_TESTING.md); legal and
compliance (Phase 6): [docs/PHASE_6_TESTING.md](../docs/PHASE_6_TESTING.md) and [docs/COMPLIANCE.md](../docs/COMPLIANCE.md);
pilots, results reports, transcript review and launch (Phase 7): [docs/PHASE_7_TESTING.md](../docs/PHASE_7_TESTING.md);
incidents: [docs/RUNBOOK.md](../docs/RUNBOOK.md).
Whole stack locally: `make dev` at the repo root.

## Health

- `GET /livez` — process is up
- `GET /readyz` — MongoDB (and Redis when `REDIS_URL` is set) reachable within 1 s, else **503** (`/health` is an alias)
- `GET /metrics` — Prometheus (Bearer `METRICS_TOKEN`; open in development). See `core/metrics.py`

## Layout

```
main.py                 FastAPI app, middleware, health
config.py               every setting + production guard (documented in .env.example)
agent/                  LiveKit voice agent (phone + browser): worker, session, prompt, telemetry;
                        sales.py + sales_worker.py = the outbound sales agent (Phase 4)
core/                   framework-light shared logic
  tenancy/              TenantConfig + resolver (number / WhatsApp id / id → tenant, cached)
  repositories/         tenant-scoped data access (TenantRepo, Repos, scoped_db)
  auth/                 users, passwords, tokens, roles, TOTP, FastAPI dependencies
  prompts/              prompt builder + vertical templates
  services/             availability, scheduling, calendar sync, tenant admin, insights (Phase 7 results report),
                        reviews (transcript review → redacted eval cases)
  tools/                AI tools shared by voice + WhatsApp (registry.run_tool is the entry point)
  integrations/         Twilio/Meta signatures, WhatsApp, SMS, e-mail, Google Calendar, outgoing webhooks, ElevenLabs voices, S3 links
  notifications.py      owner + caller notifications
  log.py                JSON logging, PII redaction, request/call ids
  observability.py      Sentry
  redis.py, ratelimit.py, dedupe.py, concurrency.py   shared state (Phase 3), memory fallback without Redis
  metrics.py            Prometheus /metrics: HTTP + call outcomes, latency, provider health
  phone.py, timeutils.py, signed_links.py
  outbound/             Phase 4 dialer: compliance gate, rules, DNC/consent, CSV import, dialer engine, sales persona + tools, funnel
  billing/              Phase 5: plans, cost model, Stripe (REST + webhooks), usage + call gate, overage metering, plan rules, billing jobs
  compliance/           Phase 6: legal document versions + acceptance, sub-processors, opt-outs from any channel,
                        data subject requests (find / export / erase), recording retention
ai/                     WhatsApp (and legacy phone) agent loop and prompts
voice/                  legacy Twilio media-stream path, STT/TTS clients
whatsapp/               Meta webhook + sending
routers/                auth, tenant (/t), platform admin, hotel + admin APIs, LiveKit, OAuth callbacks,
                        telephony (Twilio disaster-recovery TwiML, inbound SMS STOP/START), sales (/platform/sales + /public/demo-call),
                        compliance (/t/legal, /t/privacy, /public/legal, /public/site), launch (/t/insights, pilots, reviews, margins)
jobs/                   arq job worker (python -m jobs): tenant jobs, hotel reminders, WhatsApp replies
services/               hotel reminders, calendar links, tenant jobs (reminders, digest, calendar sync)
database/               Mongo connection + indexes, hotel models, legacy profile helpers
migrations/             data migrations + index creation (python -m migrations.<name>)
scripts/                operator scripts (latency_report, synthetic_call = health check + load test,
                        import_dnc_registry = load do-not-call lists for the dialer, export_eval_cases = approved
                        eval cases → tests/evals/cases, tenant_jobs_tick, opt_out)
telephony/              LiveKit SIP trunk + dispatch rule templates, Twilio setup
deploy/systemd/         legacy VPS unit files (containers replace them: ../deploy/)
tests/                  pytest suite (tests/evals: real-LLM conversation evals)
Dockerfile              backend images: targets api, worker (default), jobs, dev
```

## Data migrations

Phase 2: turn the `.env` hotel into tenant #1 (snapshot first; idempotent):

```bash
uv run python -m migrations.m0003_hotel_to_tenant --dry-run
uv run python -m migrations.m0003_hotel_to_tenant --owner-email owner@hotel.com
uv run python -m scripts.create_admin --email you@company.com     # your platform admin
```

Phase 0:

After the first Phase 0 deploy, backfill the double-booking guard once:

```bash
uv run python -m migrations.m0001_room_nights          # dry run, lists existing conflicts
uv run python -m migrations.m0001_room_nights --apply
```
