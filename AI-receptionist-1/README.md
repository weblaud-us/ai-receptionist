# AI Receptionist — backend

FastAPI API + LiveKit voice agent for a single hotel (multi-tenant arrives in Phase 2 — see
[../ROADMAP.md](../ROADMAP.md)). This README describes what exists **today**.

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
without a phone number: send the WhatsApp "call me" keyword and open the link, or set up the SIP
trunk ([telephony/README.md](telephony/README.md)).

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
every PR; `deploy.yml` deploys `main` only after CI is green. Deployment: [DEPLOY_GUIDE.md](DEPLOY_GUIDE.md).

## Health

- `GET /livez` — process is up
- `GET /readyz` — MongoDB reachable within 1 s, else **503** (`/health` is an alias)

## Layout

```
main.py                 FastAPI app, middleware, health
config.py               every setting + production guard (documented in .env.example)
agent/                  LiveKit voice agent (phone + browser): worker, session, prompt, telemetry
core/                   framework-light shared logic
  tools/                AI tools shared by voice + WhatsApp (registry.run_tool is the entry point)
  integrations/         Twilio + Meta signature checks
  log.py                JSON logging, PII redaction, request/call ids
  observability.py      Sentry
  phone.py, timeutils.py, signed_links.py
ai/                     WhatsApp (and legacy phone) agent loop and prompts
voice/                  legacy Twilio media-stream path, STT/TTS clients
whatsapp/               Meta webhook + sending
routers/                dashboard API, LiveKit token/call page
services/               reminders, calendar links
database/               Mongo connection + indexes, models, tenancy stubs
migrations/             data migrations + index creation (python -m migrations.<name>)
scripts/                operator scripts (latency_report)
telephony/              LiveKit SIP trunk + dispatch rule templates, Twilio setup
deploy/systemd/         unit files for the API and the agent worker
tests/                  pytest suite (tests/evals: real-LLM conversation evals)
Dockerfile              backend image (voice worker by default; dev target for local use)
```

## Data migrations

After the first Phase 0 deploy, backfill the double-booking guard once:

```bash
uv run python -m migrations.m0001_room_nights          # dry run, lists existing conflicts
uv run python -m migrations.m0001_room_nights --apply
```
