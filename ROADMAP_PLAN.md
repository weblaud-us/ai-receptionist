# Plan: production roadmap — real-time voice, multi-tenant SaaS, outbound sales calls

> This is the **approved plan** for producing `ROADMAP.md`. It is the short version: scope,
> decisions and phase outline. The full build instructions live in [ROADMAP.md](ROADMAP.md).

## Context

The project (`AI-receptionist-1/` backend + `dashboard/`) is a working single-hotel demo. The goal
is to sell it to local businesses in USA/Canada, UK/EU and the Middle East, make calls feel human,
and add outbound cold calling for first-party sales. Exploration of the codebase found four blockers:

- **Slow calls.** The phone path (`AI-receptionist-1/voice/twilio_handler.py`) runs one step at a
  time: 1.2 s silence wait → batch Whisper → non-streamed GPT-4o (2+ calls when a tool is used) →
  ~6 MongoDB round trips → full ElevenLabs synthesis. Roughly 3–6 s per reply, a filler phrase on
  every turn, and no barge-in.
- **Single tenant, hotel-only.** Business profile comes from `.env`; rooms/bookings are baked into
  tools, prompt, models and dashboard.
- **Not safe for production.** No Twilio/Meta signature checks, two unauthenticated endpoints
  (`/livekit/token`, `/livekit/send-whatsapp`), one plaintext admin login, no tests, broken deploy
  script (`ecosystem.config.js` missing), in-memory state, double-booking race.
- **No real outbound.** `/voice/outbound` only reads a message aloud with Polly.

## Decisions taken

| Decision | Choice |
|---|---|
| Voice engine | **LiveKit Agents + SIP** (Twilio Elastic SIP trunk for numbers) |
| Product scope | **Any local business** — hotel becomes one vertical template |
| Outbound calling | **First-party B2B sales calls** (selling this product) |
| Markets | USA/Canada, UK/EU, Middle East |

## Deliverable

One file: `ROADMAP.md` at the workspace root. No code changes in that task. Each phase gets a goal,
numbered tasks with the exact files to touch, recommended providers/settings, acceptance checks and
a time estimate for a solo developer.

## Phase outline

| Phase | Title | Est. |
|---|---|---|
| 0 | Secure and stabilise | ~1 week |
| 1 | Real-time voice on LiveKit SIP — fixes "slow" | ~2–3 weeks |
| 2 | Multi-tenant, any-business core | ~3–4 weeks |
| 3 | Production infrastructure and operations | ~1–2 weeks |
| 4 | Outbound AI sales dialer | ~2–3 weeks |
| 5 | Billing and self-serve onboarding | ~2 weeks |
| 6 | Legal and compliance (runs alongside 1–5) | ongoing |
| 7 | Pilot, launch and sales | ~2 weeks |

### Phase 0 — Secure and stabilise
Rotate every credential in `.env`; per-environment secrets; `DEBUG=false`; real `CORS_ORIGINS`.
Enforce the Twilio signature (`VERIFY_TWILIO_SIGNATURE` is declared but never read) and Meta
`X-Hub-Signature-256`. Auth on `/livekit/token` and `/livekit/send-whatsapp`
(`routers/livekit.py`); hide `/docs` in production. Remove the "claim to be human" prompt rule
(`ai/prompts.py:191-193`) and the word-stripping in `_enforce_style` (`ai/agent.py:346-351`) — AI
disclosure is legally required in the target markets. Delete dead files (`webhook.py`,
`whatsapp/prompts.py`, `utils/logger.py`); fix `deploy.sh` / `deploy.yml`; decide the git layout.
Add a pytest skeleton + CI test step; log rotation; redact PII in logs.

### Phase 1 — Real-time voice on LiveKit SIP
Numbers on Twilio → Elastic SIP trunk → LiveKit inbound trunk + dispatch rule; outbound trunk for
Phase 4. Rebuild `livekit_agent.py` as the single voice agent: streaming STT (Deepgram Nova-class)
instead of batch `whisper-1`; a fast streaming LLM benchmarked on first-token time; low-latency
streaming TTS (ElevenLabs Flash / Cartesia Sonic) replacing full-file `eleven_turbo_v2`; LiveKit
turn detector + Silero VAD loaded once in prewarm; telephony noise cancellation; preemptive
generation; interruptions on. Share one tool implementation between voice and WhatsApp
(`ai/tools.py`). Per-call speed: load tenant config and caller history once at call start, write
transcripts in the background, one DB connection per worker, collapse `_find_alternative_dates`
(up to 28 queries) into one, and say a filler only when a tool is actually running. New tools:
`transfer_to_human` (SIP REFER), `end_call`, voicemail/after-hours message taking. Persist
transcripts, per-turn latency metrics and optional recording. Retire
`voice/twilio_handler.py` after parity, behind a flag for one release.
**Acceptance:** 20 scripted calls, median response ≤ 1.0 s, p95 ≤ 1.8 s, barge-in ≤ 300 ms.

### Phase 2 — Multi-tenant, any-business core
New collections: `tenants`, `users` (roles, hashed passwords), `phone_numbers` (number → tenant),
`services`, `business_hours`, `knowledge_items`, `appointments`, `contacts`, `calls`,
`usage_events`. Add `tenant_id` to every document and query; implement the no-op resolvers in
`database/tenancy.py`; migrate the existing hotel as tenant #1. Build the prompt from tenant data
instead of the hard-coded hotel prompt, with vertical templates (clinic, salon, restaurant, home
services, hotel). Generic tools: `check_availability`, `book_appointment`, `reschedule`, `cancel`,
`answer_from_knowledge`, `take_message`, `transfer_to_human`, `send_sms/whatsapp`; hotel room tools
become a plug-in. Fix double booking (unique index or transaction) and use the tenant timezone
instead of `date.today()`. Integrations: Google Calendar / Outlook two-way sync, SMS, email.
Dashboard: real auth and roles, editable Settings (currently read-only), onboarding wizard,
knowledge editor, call log with transcript and audio, analytics, platform super-admin view.

### Phase 3 — Production infrastructure and operations
Dockerfiles for API, agent worker and dashboard; staging + production. LiveKit Cloud for media and
SIP with agents in US and EU regions; API on a managed container host; MongoDB Atlas with backups;
Redis for shared state (replacing in-memory `active_sessions`, dedupe cache, login limiter) and a
job queue (reminders with locking). Sentry, structured logs, a latency/cost dashboard, uptime
checks, a real `/health` that returns 503 when degraded. Timeouts, retries and provider fallbacks;
per-tenant concurrency limits. CI: lint, type check, tests, build, deploy; load test 20–50
concurrent calls.

### Phase 4 — Outbound AI sales dialer
Data: `leads`, `campaigns`, `call_attempts`, `dnc_list`, `consent_records`. Dialer service: queue
and pacing, per-lead local calling hours, retry rules, max attempts, LiveKit
`CreateSIPParticipant` on the outbound trunk, answering-machine detection with voicemail drop or
hang-up. A sales persona separate from the receptionist: opener with AI disclosure, qualification,
objection handling, book-a-demo tool, instant opt-out writing to `dnc_list`, human takeover.
Dashboard: CSV lead import, campaign builder, live monitor, outcomes, recordings, funnel. Number
reputation: STIR/SHAKEN attestation, CNAM, carrier reputation registration, local presence,
volume ramp-up.

### Phase 5 — Billing and self-serve onboarding
Stripe subscriptions plus metered minutes from `usage_events`; plan limits; trial; dunning.
Self-serve signup → pick number → wizard → test call, with number provisioning via the Twilio API.
A per-minute cost model (telephony + STT + LLM + TTS + LiveKit) and pricing tiers with margin.

### Phase 6 — Legal and compliance (alongside Phases 1–5)
A checklist per market, with an explicit "have a lawyer confirm" note: US TCPA treatment of AI
voices plus state mini-TCPA laws, calling hours, caller ID and recording-consent states; Canadian
CRTC automated-dialing rules and the National DNCL; UK PECR consent and TPS/CTPS screening; EU
GDPR lawful basis, DPAs, per-country ePrivacy rules and the AI Act duty to disclose; UAE and Saudi
telemarketing licensing, DNCR and restricted hours. Product-level: AI disclosure at call start,
recording notice, DNC handling, retention and deletion, privacy policy, terms, DPA template,
sub-processor list.

### Phase 7 — Pilot, launch and sales
3–5 free pilot businesses, weekly transcript review, an eval suite built from real calls. Launch
checklist, support process, status page, onboarding docs, demo number on the website.

### Appendices
Full environment-variable list (new and removed), suggested repo layout, provider comparison with
approximate per-minute costs, risk register, definition of done per phase.

## Verification
- `ROADMAP.md` exists at the workspace root and renders cleanly in VS Code preview.
- Every file path and line reference in it matches the codebase; provider and API claims that could
  not be confirmed from the installed code are marked for verification against current docs.
- No secrets or credential values appear in the file.
