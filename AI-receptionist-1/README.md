# AI Receptionist — backend

FastAPI API + LiveKit voice agent for a single hotel (multi-tenant arrives in Phase 2 — see
[../ROADMAP.md](../ROADMAP.md)). This README describes what exists **today**.

## What it does

| Channel | Path | How |
|---|---|---|
| Phone calls (legacy) | Twilio → `POST /voice/incoming` → media-stream WebSocket `/voice/stream` | batch Whisper STT → GPT → ElevenLabs TTS. Replaced by LiveKit SIP in Phase 1. |
| Browser calls | WhatsApp "call me" → signed link `/call/{room}?exp&sig` → LiveKit room | `livekit_agent.py` (Whisper → GPT → OpenAI TTS pipeline, or OpenAI Realtime) |
| WhatsApp | Meta Cloud API → `POST /webhook` | text and voice notes; replies by text or audio |
| Dashboard API | `/admin/*`, `/bookings/*`, `/rooms/*` | JWT login (`ADMIN_USERNAME`/`ADMIN_PASSWORD`) or `X-Admin-Key` |
| Reminders | background task in the API | WhatsApp reminder one day before check-in |

The AI can check availability, quote prices, book, look up and cancel bookings, and send a
WhatsApp confirmation. It always introduces itself as a **virtual assistant** and answers
honestly if asked whether it is an AI.

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
uv run uvicorn main:app --reload --port 8002      # API, docs at http://localhost:8002/docs
uv run python livekit_agent.py dev                # LiveKit agent (separate terminal)
```

For webhooks from Twilio/Meta to reach your machine, expose port 8002 with a tunnel (ngrok,
cloudflared) and set `WEBHOOK_BASE_URL` to the tunnel's https URL:
Twilio voice → `{WEBHOOK_BASE_URL}/voice/incoming`, Meta WhatsApp → `{WEBHOOK_BASE_URL}/webhook`.

> Windows note: `import livekit.agents` crashes on some Windows machines (native
> `livekit.local_inference`). Run the agent worker under WSL or Linux; the API is unaffected.

## Test, lint, type-check

```bash
uv run pytest          # in-memory MongoDB; no network, no real credentials
uv run ruff check .
uv run mypy            # core/ only for now
```

CI (`.github/workflows/ci.yml` at the repo root) runs all three plus the dashboard build on
every PR; `deploy.yml` deploys `main` only after CI is green. Deployment: [DEPLOY_GUIDE.md](DEPLOY_GUIDE.md).

## Health

- `GET /livez` — process is up
- `GET /readyz` — MongoDB reachable within 1 s, else **503** (`/health` is an alias)

## Layout

```
main.py                 FastAPI app, middleware, health
config.py               every setting + production guard (documented in .env.example)
core/                   framework-light shared logic
  integrations/         Twilio + Meta signature checks
  log.py                JSON logging, PII redaction, request/call ids
  observability.py      Sentry
  phone.py, timeutils.py, signed_links.py
ai/                     agent loop, prompts, tools (shared by voice + WhatsApp)
voice/                  legacy Twilio media-stream path, STT/TTS clients
whatsapp/               Meta webhook + sending
routers/                dashboard API, LiveKit token/call page
services/               reminders, calendar links
database/               Mongo connection + indexes, models, tenancy stubs
migrations/             one-off data migrations (python -m migrations.<name>)
deploy/systemd/         unit files for the API and the agent worker
tests/                  pytest suite
livekit_agent.py        LiveKit voice agent worker
```

## Data migrations

After the first Phase 0 deploy, backfill the double-booking guard once:

```bash
uv run python -m migrations.m0001_room_nights          # dry run, lists existing conflicts
uv run python -m migrations.m0001_room_nights --apply
```
