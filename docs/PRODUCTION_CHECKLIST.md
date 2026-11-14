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

Follow [DEPLOY_GUIDE.md](../AI-receptionist-1/DEPLOY_GUIDE.md) sections 1 and 2:

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
