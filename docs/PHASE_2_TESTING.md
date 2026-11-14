# Phase 2 — multi-tenant core: what's built and how to test it

What Phase 2 added (ROADMAP.md §7), how to switch an existing deployment over, and how to check
each acceptance item. Production steps are in [PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md).

## What's in the code

| Roadmap task | Where |
|---|---|
| 2.2 Data model | `core/tenancy/models.py` (`TenantConfig`), indexes in `database/mongodb.py` (every compound index starts with `tenant_id`) |
| 2.3 Isolation in code | `core/repositories/` (`TenantRepo` adds `tenant_id` to every query/insert/update); `core/repositories/scoped.py` (`scoped_db`) for the older hotel/admin routers; CI guard `tests/test_isolation_guard.py`; isolation suite `tests/test_api_tenancy.py` |
| 2.4 Tenant resolution | `core/tenancy/resolver.py` — dialled number, WhatsApp number id, dashboard token, slug; 60 s cache, busted on every settings change; unknown number → neutral message + Sentry warning (`agent/worker.py`) |
| 2.5 Prompt builder + verticals | `core/prompts/builder.py`, `core/prompts/verticals/` (home services, salon, restaurant, hotel, clinic, professional, generic) |
| 2.6 Knowledge base | inline in the prompt up to 60 items / 6 000 chars, otherwise the `answer_from_knowledge` tool (keyword search; vector search can replace it later); unanswered questions logged |
| 2.7 Generic tools + availability | `core/services/availability.py` (pure, DST-safe), `core/services/scheduling.py` (`slot_locks` unique index = no double booking), `core/tools/scheduling.py`, `leads.py`, `knowledge.py`, `messages.py`; hotel tools are the hotel vertical's plug-in |
| 2.8 Google Calendar | `core/integrations/google_calendar.py`, `core/services/calendar_sync.py`, callback `routers/integrations.py`; tokens encrypted (`core/crypto.py`) |
| 2.9 Notifications | one WhatsApp sender `core/integrations/whatsapp.py`, SMS `sms.py`, e-mail `email.py`, routing `core/notifications.py`; reminders + daily digest `services/tenant_jobs.py` |
| 2.10 Dashboard + auth | `core/auth/` (argon2id, 15-min access token in memory, rotating httpOnly refresh cookie, roles, TOTP 2FA), `routers/auth.py`, `routers/tenant.py`, `routers/platform.py`; dashboard pages under `dashboard/src/pages/` |
| 2.11 Hotel → tenant #1 | `migrations/m0003_hotel_to_tenant.py` |

Microsoft 365 / Outlook sync, Atlas Vector Search, Google sign-in and e-mail verification for
self-signup are **not** built (Outlook was "second" in the roadmap; signup is Phase 5).

## Switching an existing deployment over

Until migration m0003 runs, everything keeps working as the single `.env` hotel ("legacy mode").

1. Deploy. `deploy.sh` runs `m0002_indexes`, which adds the new indexes (safe on live data).
2. **Take an Atlas snapshot.**
3. Dry run, then the real thing:
   ```bash
   uv run python -m migrations.m0003_hotel_to_tenant --dry-run
   uv run python -m migrations.m0003_hotel_to_tenant --owner-email owner@hotel.com --owner-name "…"
   ```
   It prints a one-time set-password link for the owner, and a before/after count table.
4. Create your own platform admin, sign in, and turn on two-factor (Team → Two-factor sign-in):
   ```bash
   uv run python -m scripts.create_admin --email you@company.com --name "Your Name"
   ```
   Then clear `ADMIN_PASSWORD` (the bootstrap `.env` login).
5. Remove the `HOTEL_*` variables at the next deploy.

## Local testing

```bash
cd AI-receptionist-1
uv sync
uv run pytest                     # 200+ tests, in-memory Mongo
docker compose -f docker-compose.dev.yml run --rm --build tests   # adds the LiveKit tests (Linux)
cd ../dashboard && npm ci && npm run dev
```

For local dashboard sign-in over plain http set `COOKIE_SECURE=false` and
`CORS_ORIGINS=http://localhost:5173` in `.env` (production refuses `COOKIE_SECURE=false`).

## Acceptance checks (ROADMAP §7)

| Check | How |
|---|---|
| Two tenants (salon + plumber) each get their own greeting, voice, hours, services and tools on a live call | Platform → New business twice, assign each a number on the SIP trunk (`POST /platform/phone-numbers`), finish setup for each, call both numbers. The worker logs `Call … started for ten_…` with the right id |
| Isolation suite passes for every endpoint and tool | `uv run pytest tests/test_api_tenancy.py tests/test_generic_tools.py tests/test_tenancy.py tests/test_isolation_guard.py` |
| Owner configures everything from the dashboard and places a test call from the browser | Sign in as the owner → Setup wizard → "Call your assistant now" (needs `LIVEKIT_*` and `CALL_BASE_URL`) |
| Concurrent booking of the same slot: exactly one succeeds | `tests/test_scheduling.py::test_concurrent_bookings_of_the_same_slot_exactly_one_wins`; verified on a real MongoDB too (5 parallel API requests → one 200, four 409) |
| Google Calendar busy times block slots; AI bookings appear in Google | Needs `GOOGLE_OAUTH_CLIENT_ID/SECRET` + `TOKEN_ENCRYPTION_KEY`. Settings → Integrations → Connect, add a busy event, Sync now, check the slot disappears; book by phone and check the event. Unit-tested with Google mocked in `tests/test_phase2_integrations.py` |
| Owner gets an SMS for a taken message within 30 s of hang-up | Set the owner's mobile in Settings → Notifications and `TWILIO_SMS_FROM` (US: A2P 10DLC or verified toll-free first). The message is sent the moment the assistant saves it |
| The hotel works exactly as before as tenant #1 | After m0003: call the hotel number, book a room, check Bookings in the dashboard. `tests/test_api_tenancy.py::test_hotel_tenant_routes_after_migration` |

## Things to know

- **Settings apply to the next call**, never mid-call (cache: 60 s, busted on save).
- **Platform admins** must use two-factor (`REQUIRE_ADMIN_MFA=true`). The `.env` bootstrap admin and
  `X-Admin-Key` are exempt — remove them once real admins exist.
- **Owner instructions** are fenced in the prompt and capped at 1 500 characters; they can't
  override honesty or safety rules.
- **Caller confirmations** go by WhatsApp when the business has a number, otherwise SMS; the
  consent basis (transactional, from the booking call) is stored on the contact.
- ⚠ US healthcare (clinic vertical): don't sell until BAAs are signed with every vendor.
