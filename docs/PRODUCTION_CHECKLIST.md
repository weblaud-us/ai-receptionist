# Production checklist — do this before the first real deploy

Nothing here is needed for development or testing. Do it once all roadmap phases are done and
you are ready to go live. Server commands are in
[AI-receptionist-1/DEPLOY_GUIDE.md](../AI-receptionist-1/DEPLOY_GUIDE.md); this file is the
list of what to do and in what order.

When a later phase adds a production step (Redis, LiveKit SIP trunks, Stripe and so on), add it
under its own heading at the end of this file.

---

## 1. Credentials (Roadmap Task 0.2)

The development keys have been on laptops and in chat logs. Production gets **new** keys,
created only for production. Create them in this order, because the first ones cost money or
send messages.

- [ ] **OpenAI:** create a production project, a project-scoped key, and a monthly spend cap.
- [ ] **ElevenLabs:** a new API key, limited to TTS if that option exists.
- [ ] **Twilio:** create an API Key + Secret for REST calls. Keep `TWILIO_AUTH_TOKEN`: the app
      uses it to check webhook signatures.
- [ ] **Meta WhatsApp:** a permanent **System User** token (not a personal user token), plus the
      **App Secret** (App Dashboard → Settings → Basic) as `META_APP_SECRET`.
- [ ] **`META_WA_VERIFY_TOKEN`:** a new random string of 32+ characters.
- [ ] **LiveKit:** a new API key and secret for the production project.
- [ ] **MongoDB Atlas:** one database user per environment, each with `readWrite` on its own
      database only. Under Network Access, remove `0.0.0.0/0` and allow only the server IPs.
- [ ] **`SECRET_KEY`** and **`ADMIN_SECRET_KEY`:** generate each with
      `python -c "import secrets;print(secrets.token_urlsafe(64))"`.
- [ ] **`ADMIN_PASSWORD`:** 20+ characters. Phase 2 replaces it with real user accounts.
- [ ] **Deepgram:** a new key when Phase 1 starts using it.
- [ ] Once production is running on the new keys, **revoke the old development keys** that were
      ever in a shared `.env`.
- [ ] Delete `AI-receptionist-1/.env.bak-before-phase0` from your laptop. It holds the
      pre-Phase-0 keys. Git ignores it, but it is still on disk.

## 2. Secret storage (Roadmap Task 0.3)

- [ ] Pick a secret manager. Doppler or Infisical is recommended (free tier, per-environment
      configs, audit log).
- [ ] Create separate `dev`, `staging` and `prod` configs. Production secrets never go on a
      laptop.
- [ ] The API and the agent worker read the same production config. Until a secret manager is
      in place, that is `/etc/receptionist/prod.env` (owned by root, group `receptionist`,
      `chmod 640`).

## 3. Production environment values

The app **refuses to start** in production if any of these are wrong (`config.py` →
`_production_guard`), so mistakes show up at boot, not later.

| Variable | Production value |
|---|---|
| `APP_ENV` | `production` |
| `DEBUG` | `false` |
| `LOG_LEVEL` | `INFO` (DEBUG would log call transcripts) |
| `LOG_FORMAT` | `json` |
| `SECRET_KEY` | 32+ random characters, never the default |
| `ADMIN_SECRET_KEY` | empty, or 32+ random characters |
| `CORS_ORIGINS` | the dashboard URL(s), e.g. `https://app.YOUR_DOMAIN.com`. Never `*` |
| `WEBHOOK_BASE_URL` | the public `https://api.YOUR_DOMAIN.com`. Twilio signature checks depend on it being exact |
| `CALL_BASE_URL` | the public URL guests open for browser calls |
| `VERIFY_TWILIO_SIGNATURE` | `true` |
| `VERIFY_TWILIO_WS_SIGNATURE` | `true` (see the first-call check in section 6) |
| `META_APP_SECRET` | required while `ENABLE_WHATSAPP=true` |
| `MONGODB_URI` / `MONGODB_DB_NAME` | the production Atlas user and database |
| `SENTRY_DSN` | the production backend project DSN |

Dashboard (set at build time):

| Variable | Production value |
|---|---|
| `VITE_API_URL` | `https://api.YOUR_DOMAIN.com` |
| `VITE_SENTRY_DSN` | the production dashboard project DSN |
| `VITE_APP_ENV` | `production` |

## 4. Server and hosting

> Phase 3 replaced this section with containers: follow **"Phase 3 — production infrastructure"**
> at the end of this file and [deploy/README.md](../deploy/README.md). The systemd steps below
> apply only to an existing VPS that hasn't moved yet.

Legacy VPS — [DEPLOY_GUIDE.md](../AI-receptionist-1/DEPLOY_GUIDE.md) sections 1 and 2:

- [ ] Server with an unprivileged `receptionist` user, `uv`, and the repo checked out at
      `/opt/receptionist` using a read-only deploy key.
- [ ] Install both systemd units from `AI-receptionist-1/deploy/systemd/`
      (`receptionist-api`, `receptionist-agent`) and enable them.
- [ ] Nginx with TLS (Let's Encrypt), using the `X-Forwarded-For` config from the guide so the
      login rate limiter sees real client IPs.
- [ ] DNS for the API and the dashboard.
- [ ] Decide where the dashboard is hosted (static host or the same Nginx) and how it is built
      there. `deploy.sh` currently deploys only the backend.
- [ ] Phase 3 replaces this VPS setup with containers. If you get there first, follow Phase 3
      instead and skip the systemd steps.

## 5. GitHub

> Phase 3: deploys are tag-based. Use the GitHub steps in the Phase 3 section; the `VPS_*`
> secrets and the `workflow_run` note below are the old flow.

- [ ] Repo → Settings → Environments → create a `production` environment (add yourself as a
      required reviewer if you want to approve each deploy).
- [ ] Add the secrets from DEPLOY_GUIDE.md section 3: `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`,
      `VPS_PORT`, `APP_DIR`.
- [ ] Protect `main`: require the `ci` workflow to pass before merging.
- [ ] **Turn automatic deploys back on.** In `.github/workflows/deploy.yml`, restore the
      `workflow_run` trigger so a green CI run on `main` deploys. It was switched to manual-only
      while development is under way.

## 6. First deploy and verification

- [ ] Run the deploy (Actions → deploy → Run workflow), or run `./AI-receptionist-1/deploy.sh`
      on the server.
- [ ] Both services restart cleanly: `systemctl status receptionist-api receptionist-agent`.
- [ ] `curl https://api.YOUR_DOMAIN.com/readyz` returns 200. `/docs` returns 404.
- [ ] `curl -X POST https://api.YOUR_DOMAIN.com/voice/incoming -d CallSid=fake` returns **403**.
- [ ] A POST to `/webhook` without a valid `X-Hub-Signature-256` returns **403**.
- [ ] **Data migration, once:** run `python -m migrations.m0001_room_nights` against the
      production database, fix any CONFLICT lines it reports, then run it with `--apply`
      (DEPLOY_GUIDE.md, "First data migration").
- [ ] Point the Twilio number's voice webhook and the Meta WhatsApp webhook at the production
      URLs.
- [ ] **First real phone call:** confirm the media-stream WebSocket signature check accepts
      Twilio's handshake. If real calls are rejected, set `VERIFY_TWILIO_WS_SIGNATURE=false`
      (the HTTP webhook stays protected) and check Twilio's Media Streams security docs.
- [ ] The AI introduces itself as a virtual assistant and, when asked, says it is an AI.
- [ ] Trigger a test exception and confirm it appears in Sentry **with no phone numbers or
      e-mail addresses** in it.

## 7. Before taking real customers

- [ ] Phase 6 legal checklist signed off by a lawyer for each target market.
- [ ] Atlas backups on and a restore tested.
- [ ] Uptime monitor on `/readyz`.

---

## Phase 1 — real-time voice (LiveKit SIP)

Development and testing use a dev LiveKit project and a dev Twilio trunk. For production:

- [ ] **Keys:** production keys for Deepgram, ElevenLabs (and Cartesia if used as fallback TTS),
      and a separate production LiveKit project (`LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET`).
- [ ] **Telephony:** a `livekit-prod` Twilio Elastic SIP trunk plus production inbound trunk,
      dispatch rule and outbound trunk, created from `AI-receptionist-1/telephony/` with
      production secrets ([telephony/README.md](../AI-receptionist-1/telephony/README.md)).
      Set `LIVEKIT_SIP_OUTBOUND_TRUNK_ID`.
- [ ] **Transfers:** `TRANSFER_PHONE_NUMBER` (and `TRANSFER_HOURS`) set; transfer mode enabled on
      the Twilio trunk; voicemail set up on the staff line (a cold transfer that nobody answers
      can't come back to the agent).
- [ ] **Trust Hub:** Business Profile approved, SHAKEN/STIR A-attestation and CNAM on every number.
- [ ] **Secure trunking:** TLS + SRTP on both trunks once the plain setup works.
- [ ] **Worker:** `LIVEKIT_NUM_IDLE_PROCESSES=2` or more; the worker in the same cloud region as
      LiveKit and MongoDB (measure the ping from the worker host).
- [ ] **Owner notifications:** `OWNER_NOTIFY_WHATSAPP` set so new messages reach the business.
- [ ] **Recording (only if offered):** bucket in the customer's region, a lifecycle rule deleting
      objects after `RECORDING_RETENTION_DAYS`, `ENABLE_CALL_RECORDING=true`, and the legal review
      of recording consent per market (Phase 6). The greeting then announces recording.
- [ ] **Acceptance (Roadmap Task 1.12.c), on the production setup:** the 20-call script from real
      phones, then `python -m scripts.latency_report --channel phone`: p50 ≤ 1.0 s, p95 ≤ 1.8 s,
      tool turns ≤ 1.6 s, greeting ≤ 0.7 s; 5/5 transfers succeed; a deploy during a live test call
      drops nothing.
- [ ] **Evals in CI:** add an `EVAL_OPENAI_API_KEY` repository secret so `tests/evals` runs on every PR.
- [ ] **Cutover (Task 1.13):** move one number to the SIP trunk, run a week, move the rest, then set
      `LEGACY_TWILIO_VOICE=false`. One release later, delete `voice/twilio_handler.py`,
      `voice/stt_tts.py`, the `/voice/*` routes, `call_sessions` and `audioop-lts`.


## Phase 2 — multi-tenant core

- [ ] Atlas snapshot, then `migrations.m0003_hotel_to_tenant --dry-run`, then the real run with
      `--owner-email`. Send the owner the printed link.
- [ ] `scripts.create_admin` for each platform admin; each turns on two-factor; clear `ADMIN_PASSWORD`
      and `ADMIN_SECRET_KEY` once nothing uses them.
- [ ] `TOKEN_ENCRYPTION_KEY` set (Fernet key) and stored in the secret manager. Losing it means
      every calendar must be reconnected and every two-factor setup redone.
- [ ] `COOKIE_SECURE=true`; `COOKIE_SAMESITE=lax` if dashboard and API share a site (app./api.),
      otherwise `none`; `CORS_ORIGINS` lists the dashboard origin; `DASHBOARD_URL` set for links.
- [ ] Google Calendar: OAuth client (Web), redirect URI `{WEBHOOK_BASE_URL}/integrations/google/callback`,
      **app verification submitted** (weeks). ⚠ Verify the current scope classification.
- [ ] SMS: `TWILIO_SMS_FROM` on an A2P 10DLC-registered campaign or a verified toll-free number (US);
      per-country sender rules elsewhere. E-mail: `RESEND_API_KEY`, `EMAIL_FROM`, SPF/DKIM/DMARC.
- [ ] WhatsApp per tenant: set `integrations.whatsapp.phone_number_id` with `PATCH /platform/tenants/{id}`.
      Business-initiated messages outside 24 h need approved templates (not built yet).
- [ ] Every number on the SIP trunk is assigned in `phone_numbers` (Platform page); call an
      unassigned one and confirm the neutral message and the Sentry warning.
- [ ] Remove the `HOTEL_*` variables after the hotel works as tenant #1.
- [ ] ⚠ No US healthcare tenants until BAAs are signed with every vendor (ROADMAP Task 2.1).

## Phase 3 — production infrastructure

Details and acceptance checks: [PHASE_3_TESTING.md](PHASE_3_TESTING.md). Host setup:
[deploy/README.md](../deploy/README.md). Incidents: [RUNBOOK.md](RUNBOOK.md).

**Hosting**
- [ ] One Docker host per environment (staging, production), same region as LiveKit and Atlas
      (`WORKERS=2`–`4` in the `.deploy` file, sized from the load test);
      `/etc/receptionist/<env>.env` and `<env>.deploy` written; DNS for API and dashboard.
- [ ] **Atlas M10+** for production (shared tiers have no SLA), continuous backup with
      point-in-time restore on, TLS only, IP allowlist = the hosts (or a private endpoint),
      separate DB users per environment.
- [ ] **Redis:** the bundled container works; a managed Redis (Upstash / Redis Cloud /
      ElastiCache, TLS `rediss://`) is better. `REDIS_URL` set — production refuses to boot without it.
- [ ] `LEGACY_TWILIO_VOICE=false` before running two API replicas (the old phone path keeps call
      state in one process).
- [ ] Cloudflare (proxied) in front of the API: WAF managed rules on, a rate-limit rule on
      `/auth/*` and `/webhook`, and know where "Under attack" mode is for emergencies.

**GitHub**
- [ ] Environments `staging` and `production` with secrets `DEPLOY_HOST`, `DEPLOY_USER`,
      `DEPLOY_SSH_KEY`, `DEPLOY_PORT`, `APP_DIR` and variables `API_URL` (+ `SENTRY_DSN_DASHBOARD`).
      Required reviewer on `production`.
- [ ] Protect `main` (CI must pass) and protect tags `v*` (only you can push them).
- [ ] Dependabot alerts and secret scanning on (repo → Security). Run gitleaks over the **full
      history** once: `docker run --rm -v "$PWD:/repo" ghcr.io/gitleaks/gitleaks detect --source /repo`.
      Anything it finds must be rotated, not just deleted.

**Resilience**
- [ ] `ANTHROPIC_API_KEY` set (LLM fallback) and `VOICE_LLM_FALLBACK_MODEL` passing the eval suite;
      `CARTESIA_API_KEY` or `DEEPGRAM_API_KEY` set so TTS has a fallback.
- [ ] `fallback_app.py` running on a **second, independent host/provider**
      ([deploy/README.md](../deploy/README.md), "Independent telephony fallback"), and the Twilio
      trunk's Disaster Recovery URL pointing at it — not at the main host, which may be the thing
      that is down. ⚠ Verify current Elastic SIP Trunking DR settings.
- [ ] Each business's "Max calls at once" (Platform page) matches its plan.
- [ ] `TOKEN_ENCRYPTION_KEY` set before the first Phase 3 deploy: the deploy encrypts any stored
      guest ID numbers with it (migration m0004) and refuses if plaintext exists without it.
- [ ] `EVAL_ANTHROPIC_API_KEY` repository secret, so CI runs the evals on the fallback LLM too.
- [ ] Ask OpenAI / Anthropic / ElevenLabs / Deepgram for higher rate limits **before** the load test.

**Monitoring**
- [ ] `METRICS_TOKEN` set; Grafana Cloud (or Prometheus) scraping `/metrics` with
      [deploy/monitoring/prometheus.yml](../deploy/monitoring/prometheus.yml); alerts from
      [alerts.yml](../deploy/monitoring/alerts.yml) routed to your phone; the dashboard from
      [dashboard.md](../deploy/monitoring/dashboard.md).
- [ ] `LIVEKIT_PROMETHEUS_PORT=9100` and the workers scraped (for `VoiceWorkerDown`).
- [ ] Uptime monitor on `/readyz` every minute; public status page (Better Stack / Instatus).
- [ ] Synthetic call every 15 min with `--heartbeat` (cron on a machine outside the production
      host), heartbeat grace 35 min → phone.
- [ ] Sentry projects for API, worker, jobs and dashboard; release = git SHA (the images set it).
- [ ] JSON logs shipped to Better Stack / Axiom / Loki (Docker log driver or an agent on the host).

**Accounts and money**
- [ ] **MFA on every provider account:** Twilio, LiveKit, OpenAI, Anthropic, ElevenLabs, Deepgram,
      Cartesia, Atlas, Meta, Resend, GitHub, Cloudflare, the registrar, the host. These accounts
      *are* the company.
- [ ] Spend caps / budget alerts at 2× normal daily spend on OpenAI, Anthropic, ElevenLabs,
      Deepgram, Twilio (plus a low-balance alert), LiveKit.

**Acceptance (on staging or production)**
- [ ] Deploy during 5 live test calls → 0 dropped.
- [ ] Kill a worker mid-call → the next call is answered by the other worker within 1 s.
- [ ] Primary TTS key revoked → calls continue on the fallback voice; the alert fires.
- [ ] 50 concurrent calls, p95 response latency ≤ 1.8 s.
- [ ] Deliberately break something → synthetic call / alerts reach your phone.
- [ ] Atlas restore test done and timed (write the time in RUNBOOK.md); repeat monthly.
- [ ] Incident-response contacts filled in at the end of RUNBOOK.md.

## Phase 4 — outbound sales dialer

Details: [PHASE_4_TESTING.md](PHASE_4_TESTING.md). Keep `OUTBOUND_DIALER_ENABLED=false` until
every box in **Legal** and **Numbers** is ticked.

**Legal** ⚖
- [ ] A telemarketing lawyer has reviewed, for each market you will call: the call types and
      countries (`core/outbound/rules.py`, `SALES_*_COUNTRIES`), the state calling windows and
      `SALES_BLOCKED_US_STATES`, the openers and the sales prompt (`core/outbound/persona.py`), the
      consent wording on the form (`routers/sales.py` `CONSENT_TEXT`) and the lead sources.
- [ ] Registered as a seller with the FTC National Do Not Call Registry (SAN); area codes downloaded
      and loaded (`python -m scripts.import_dnc_registry`); a monthly reminder to refresh them.
- [ ] `SALES_EMAIL_FOOTER` has your postal address and how to opt out (CAN-SPAM).
- [ ] Recording: if `agent.recording.enabled` is on for the sales tenant, the opener announces it
      (all-party-consent states).

**Numbers and reputation**
- [ ] A separate Twilio **subaccount** for sales, its own Elastic SIP trunk and LiveKit outbound
      trunk → `SALES_SIP_OUTBOUND_TRUNK_ID` (production refuses to start the dialer without it).
- [ ] A small, stable pool of local-area-code numbers; SHAKEN/STIR **A-attestation** via Trust Hub;
      **CNAM** set to your brand; numbers registered with the carrier analytics engines
      (Free Caller Registry). ⚠ VERIFY current registration paths.
- [ ] Inbound calls to the sales numbers ring a person (or your demo line) — never unanswered.
- [ ] `SALES_MAX_CALLS_PER_NUMBER_PER_DAY` starts at 20–30; campaigns start at 1–2 calls at once.

**Setup**
- [ ] Your business provisioned as a tenant (`SALES_TENANT_ID`): hours, timezone, transfer number,
      Google Calendar connected, a 15-minute `Demo` service.
- [ ] Twilio Lookup credentials set; `SALES_DEMO_LINE`, `SALES_FOUNDER_NAME`, `SALES_DEMO_MEETING_URL`.
- [ ] `RUN_EVALS=1 pytest tests/evals/test_sales_evals.py` passes on the production model.
- [ ] Alerts `OutboundOptOutsHigh` / `OutboundCallsFailing` routed to your phone.

**Acceptance**
- [ ] Gate and opt-out tests green in CI (they run on every PR).
- [ ] One real call to your own landline: listen in, take over, opt out → number on the DNC list.
- [ ] AMD ≥ 90% correct on 100 hand-labelled calls (Results → "Who really answered?").
- [ ] 50 real calls: answer rate, conversation rate and demos recorded on the Results page.
- [ ] Caller ID shows your name; no spam label after week 1.
