# Telephony: testing phone calls (free in development, Twilio in production)

`APP_ENV` decides where phone calls come from. Nothing else in the code changes between modes: the
agent finds the business from the dialled number either way.

| `APP_ENV` | Provider | Calls come from | Cost |
|---|---|---|---|
| `development` | `softphone` | Linphone on your PC, dialling LiveKit's SIP address directly | No telephony cost, no Twilio account |
| `staging`, `production` | `twilio` | Real phone → Twilio number → Elastic SIP trunk → LiveKit | Twilio number + per-minute charges |

Other values: `TELEPHONY_PROVIDER=livekit` uses a US number rented in LiveKit Cloud (no Twilio: Part 4), and
`TELEPHONY_PROVIDER=twilio` in a dev `.env` tries Twilio from your machine. `TELEPHONY_PROVIDER=softphone` is refused in production: the app won't boot.

```
development:  Linphone ──SIP/TCP──▶ LiveKit Cloud SIP ──▶ room "call-…" ──▶ agent (agent/worker.py)
production:   caller ─PSTN─▶ Twilio number ─Elastic SIP trunk─▶ LiveKit Cloud SIP ──▶ (same)
```

One command creates the LiveKit side for whichever mode is active:

```bash
uv run python -m scripts.setup_telephony --dry-run   # show what it would do, change nothing
uv run python -m scripts.setup_telephony             # do it (safe to re-run: unchanged objects are skipped)
```

> **What "free" covers.** The phone leg costs nothing. The agent still calls OpenAI and Deepgram
> for every test call (cents per call, or your free Deepgram credit), and LiveKit Cloud counts SIP minutes against your
> plan's quota (check the plan's limits in the LiveKit Cloud dashboard).

## What each mode can test

| | Softphone (dev) | Twilio (prod) |
|---|---|---|
| Greeting, booking, tools, interruptions, hang-up | ✅ | ✅ |
| Tenant lookup by dialled number, unassigned-number path | ✅ | ✅ |
| `sip.*` participant attributes, `call-…` rooms, BVCTelephony noise cancellation | ✅ | ✅ |
| Latency numbers | ⚠ Good for tuning; a PC on broadband is faster than a mobile network | ✅ Real numbers |
| Real caller ID, mobile/landline audio quality, carrier behaviour | ❌ | ✅ |
| `transfer_to_human` (SIP REFER to a real phone), outbound calls | ❌ | ✅ |
| Spam labelling, SHAKEN/STIR | ❌ | ✅ |

Browser calls (the dashboard's **Test call** button, or the WhatsApp "call me" link) need no telephony
at all and work in both modes. Use them for day-to-day prompt and tool work; use the softphone only
when you're checking the SIP path itself.

---

# Part 0: Accounts and environment variables

Most "it doesn't work" problems in development are a wrong or empty value in `.env`. Fill it in with
this section, then **let the checker tell you which values work**:

```bash
cd AI-receptionist-1
uv run python -m scripts.check_env
```

It tests each key against the real service (read-only; it never prints a key) and prints ✅ / ⚠️ / ❌
with a hint. Re-run it after every `.env` change. Everything required must be ✅ before you start the
worker. If you see ❌ next to a service, jump to its row below.

## 0.1 Accounts to create (development)

| Service | Why it's needed | Required? | Where to create it | Cost |
|---|---|---|---|---|
| **MongoDB** | Stores businesses, calls, bookings | Yes | https://www.mongodb.com/cloud/atlas → free **M0** cluster (or a local MongoDB) | Free tier |
| **OpenAI** | The agent's brain (LLM) and fallback speech | Yes | https://platform.openai.com → sign up → **add billing credit** | Pay per use (cents per call) |
| **LiveKit Cloud** | Runs the rooms, the SIP endpoint and noise cancellation | Yes | https://cloud.livekit.io → create a **dev** project | Free plan (check its SIP-minute quota) |
| **Deepgram** | Speech-to-text **and** the agent's voice (Aura) | Yes | https://console.deepgram.com | Free starting credit (check the console for your balance) |
| **Kokoro** (local) | Free backup voice that runs on your PC | Optional | No account: a Docker container, see [0.5](#05-the-agents-voice-deepgram-first-kokoro-as-backup) | Free |
| **Linphone** | The "phone" you call from | Yes for SIP tests | https://www.linphone.org → Download | Free, no account |
| Sentry, Resend, Google, Meta, ElevenLabs, Cartesia, S3 | Error alerts, e-mail, calendar, WhatsApp, other voices, recordings | **No**: skip all of these for call testing | | |
| **Twilio** | Real phone numbers | **Production only** (Part 2) | https://www.twilio.com | |

A ChatGPT subscription is **not** the same as OpenAI API credit. The API has its own billing page.

## 0.2 The variables, where to find each

`AI-receptionist-1/.env` (copy `.env.example` to `.env` first if you don't have one). No quotes
around values, no spaces around `=`. Don't put `#` inside a value: Docker and `.env` parsers treat
it as the start of a comment.

### A. Required for test calls

| Variable | What to put | Where to find it |
|---|---|---|
| `APP_ENV` | `development` | You choose. This is the switch that makes calls use the softphone |
| `SECRET_KEY` | 32+ random characters | Generate: `uv run python -c "import secrets; print(secrets.token_urlsafe(64))"`. Don't leave the text from `.env.example` |
| `MONGODB_URI` | `mongodb+srv://USER:PASSWORD@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority` | Atlas → **Database** → your cluster → **Connect** → **Drivers** → copy the string and replace `<password>`. Create the user under **Database Access**. Under **Network Access** add your IP (or `0.0.0.0/0` for dev only). If the password has `@ : / ?`, URL-encode it (`@` → `%40`) |
| `MONGODB_DB_NAME` | `hotel_ai_dev` | You choose. Atlas creates it on first write |
| `OPENAI_API_KEY` | `sk-proj-…` | https://platform.openai.com/api-keys → **Create new secret key**. Shown once. Needs billing credit at https://platform.openai.com/settings/organization/billing. `❌ 401` from the checker means the key is wrong or revoked; `⚠️ 429` means no credit |
| `DEEPGRAM_API_KEY` | key string | console.deepgram.com → **API Keys** → **Create a New API Key**. One key does speech-to-text **and** the Aura voice |
| `VOICE_TTS_PROVIDER` | `deepgram` | The voice tried **first**. Options: `deepgram`, `kokoro`, `elevenlabs`, `cartesia`, `openai` |
| `VOICE_TTS_FALLBACKS` | `kokoro,openai` | Voices tried next, left to right, if the first fails. A voice with no key / URL is skipped. See [0.5](#05-the-agents-voice-deepgram-first-kokoro-as-backup) |
| `LIVEKIT_URL` | `wss://<project>.livekit.cloud` | cloud.livekit.io → your project → **Settings** (the WebSocket URL). Must start with `wss://` |
| `LIVEKIT_API_KEY` | `API…` | Same project → **Settings → API Keys** → **Create key**. Key and secret come as a pair |
| `LIVEKIT_API_SECRET` | long string | Shown **once** when you create the key. If lost, create a new key. Key, secret and URL must all come from the **same** project |
| `SOFTPHONE_SIP_USERNAME` | digits, e.g. `15557654321` | You choose. Linphone logs in with it; it also shows up as the fake caller number |
| `SOFTPHONE_SIP_PASSWORD` | 16+ letters and digits | Generate: `uv run python -c "import secrets; print(secrets.token_hex(16))"`. Anyone with your SIP address and this password can call your agent |
| `SOFTPHONE_TEST_NUMBERS` | `+15550000001,+15550000002` | You choose. Fake numbers you dial. Keep a second one unassigned for the unassigned-number test |

### B. Strongly recommended

| Variable | What to put | Where to find it |
|---|---|---|
| `CALL_BASE_URL` | `http://localhost:8002` | Where your API runs. The dashboard's **Test call** button builds its link from this, so it must be a URL **your browser can open**. A placeholder like `https://your-public-domain.example.com` gives a dead link. (A tunnel URL also works, e.g. ngrok) |
| `ADMIN_PASSWORD` | any password | Bootstrap dashboard login before a real admin exists (user name `admin`). Better: create a real admin with `uv run python -m scripts.create_admin --email you@example.com`, then clear this |
| `DASHBOARD_URL` | `http://localhost:5173` | Where the dashboard runs (`npm run dev` in `dashboard/`) |
| `CORS_ORIGINS` | `http://localhost:5173` | Same origin as the dashboard, otherwise the browser blocks its API calls |
| `LOG_FORMAT` | `text` | Readable logs while developing |

### C. Leave empty or default in development

`TELEPHONY_PROVIDER` (empty), `TRANSFER_PHONE_NUMBER`, `CARTESIA_*`, `SENTRY_DSN`, `RESEND_API_KEY`,
`GOOGLE_*`, `TOKEN_ENCRYPTION_KEY`, `META_*`, `RECORDING_*`, `TWILIO_*`, `TWILIO_SIP_*`. None of them
blocks a call. `WEBHOOK_BASE_URL` is only used for Twilio and WhatsApp webhooks, so any value is fine
for softphone tests unless the browser **Test call** falls back to it (set `CALL_BASE_URL` and it
won't).

**WhatsApp placeholders:** `.env.example` ships `META_WA_ACCESS_TOKEN=REPLACE_ME`. A placeholder is
not empty, so a tool that sends a WhatsApp confirmation will try, and fail, with it. In development
set `META_WA_ACCESS_TOKEN=` (empty) and the tools skip the send cleanly.

## 0.3 Common reasons a value "doesn't work"

| Checker says | Usual cause |
|---|---|
| ❌ OpenAI … HTTP 401 | Key typed wrong, revoked, or from a different account. Create a new key. Check there is no trailing space or quote |
| ⚠️ OpenAI … 429 | No billing credit on the API account |
| ❌ MongoDB … | Wrong password, special characters not URL-encoded, or your IP isn't allowed in Atlas → **Network Access**. From Docker, a *local* MongoDB must be `host.docker.internal`, not `localhost` |
| ❌ LiveKit … | URL, key and secret from different projects, or `https://` instead of `wss://` |
| ❌ ElevenLabs … voice id | `ELEVENLABS_VOICE_ID` isn't in your account. Pick one from **Voices** |
| ⚠️ ElevenLabs … restricted | The API key lacks **Text to Speech** permission |
| ⚠️ Voice … is not usable | `VOICE_TTS_PROVIDER` has no key / URL, so the next voice in the order is used. Add the key, or change the setting |
| ❌ Kokoro … can't reach the server | The container isn't running or is still loading: `docker compose -f docker-compose.dev.yml --profile local-tts up -d kokoro`, wait a minute |
| Calls still use ElevenLabs | `ELEVENLABS_API_KEY` is set, and businesses default to ElevenLabs first. Empty that key |
| ❌ SECRET_KEY placeholder | The text from `.env.example` is still there. Generate a random one |
| ❌ Softphone … not set | Add the three `SOFTPHONE_*` variables |
| Checker is green but the worker behaves as if `.env` is unchanged | Docker only reads `.env` at start. Restart: `docker compose -f docker-compose.dev.yml up --build agent` |

## 0.4 Minimal working `.env` for development

```ini
APP_ENV=development
LOG_FORMAT=text
SECRET_KEY=<token_urlsafe(64) output>

MONGODB_URI=mongodb+srv://USER:PASSWORD@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority
MONGODB_DB_NAME=hotel_ai_dev

OPENAI_API_KEY=sk-proj-...
DEEPGRAM_API_KEY=...

# Voice (0.5): Deepgram Aura first, local Kokoro as backup, OpenAI as the last resort
VOICE_TTS_PROVIDER=deepgram
VOICE_TTS_FALLBACKS=kokoro,openai
DEEPGRAM_TTS_MODEL=aura-2-andromeda-en
KOKORO_BASE_URL=http://kokoro:8880/v1
ELEVENLABS_API_KEY=

LIVEKIT_URL=wss://<project>.livekit.cloud
LIVEKIT_API_KEY=API...
LIVEKIT_API_SECRET=...
LIVEKIT_AGENT_NAME=receptionist

SOFTPHONE_TEST_NUMBERS=+15550000001,+15550000002
SOFTPHONE_SIP_USERNAME=15557654321
SOFTPHONE_SIP_PASSWORD=<token_hex(16) output>

CALL_BASE_URL=http://localhost:8002
DASHBOARD_URL=http://localhost:5173
CORS_ORIGINS=http://localhost:5173
ADMIN_PASSWORD=<temporary dev password>
META_WA_ACCESS_TOKEN=
```

Then run `uv run python -m scripts.check_env` and fix every ❌ before going on.

## 0.5 The agent's voice: Deepgram first, Kokoro as backup

The voice order is set entirely in `.env`. Nothing is hard-coded.

```
VOICE_TTS_PROVIDER   → tried first
VOICE_TTS_FALLBACKS  → tried next, left to right
```

If a voice has no key (or no Kokoro URL), it's skipped. If the active voice fails during a call (network
error, out of credit, server down), LiveKit switches to the next one without dropping the call.

| Voice | Setting | Needs | Cost | Notes |
|---|---|---|---|---|
| `deepgram` (Aura-2) | `DEEPGRAM_TTS_MODEL` | `DEEPGRAM_API_KEY` (the same key as speech-to-text) | Free starting credit, then pay per use | Streaming, low latency. English voices; see Deepgram's [model list](https://developers.deepgram.com/docs/tts-models) |
| `kokoro` (Kokoro-82M) | `KOKORO_VOICE`, `KOKORO_MODEL` | `KOKORO_BASE_URL` and the Docker container | Free, runs on your PC | CPU only on most laptops, so slower than a cloud voice (first sound often 1 to 3 s on older CPUs). A backup, not the first choice |
| `openai` | `OPENAI_TTS_VOICE` | `OPENAI_API_KEY` | Cents per call | Slowest first sound |
| `elevenlabs`, `cartesia` | see `.env.example` | their API keys | | Optional extras |

**Recommended dev setup:**

```ini
VOICE_TTS_PROVIDER=deepgram
VOICE_TTS_FALLBACKS=kokoro,openai
DEEPGRAM_API_KEY=<your key>
DEEPGRAM_TTS_MODEL=aura-2-andromeda-en       # the model name is the voice, e.g. aura-2-luna-en, aura-2-orion-en
KOKORO_BASE_URL=http://kokoro:8880/v1         # empty = Kokoro off
KOKORO_VOICE=af_heart
ELEVENLABS_API_KEY=                           # empty, otherwise businesses still prefer ElevenLabs first
```

So calls speak with Deepgram Aura. If Deepgram has a problem, Kokoro takes over. If Kokoro isn't
running, OpenAI does.

### Start the Kokoro backup

Kokoro runs as a container next to the worker (nothing to install on Windows besides Docker):

```bash
cd AI-receptionist-1
docker compose -f docker-compose.dev.yml --profile local-tts up -d kokoro
docker compose -f docker-compose.dev.yml logs -f kokoro       # wait until it says it is ready (about a minute)
uv run python -m scripts.check_env                             # ✅ Kokoro server is up
```

- The first start downloads the image (a few GB). After that it starts quickly.
- The worker container reaches it as `http://kokoro:8880/v1`. If you run the worker directly on your PC
  (Linux/macOS), use `http://localhost:8880/v1`.
- Stop it with `docker compose -f docker-compose.dev.yml --profile local-tts stop kokoro`. Leave
  `KOKORO_BASE_URL` set or empty as you like: a stopped server just means that voice fails over to
  the next one, and an empty URL skips it.
- To try a different Kokoro voice, change `KOKORO_VOICE` (for example `af_bella`, `af_sky`, `am_adam`).
  It must be a voice your Kokoro version has.

### Check it works

1. `uv run python -m scripts.check_env` should show ✅ Deepgram, ✅ Kokoro and
   ✅ Voice `order: deepgram → kokoro → openai`.
2. Restart the worker: `docker compose -f docker-compose.dev.yml up --build agent`. Each call logs
   `Voice order: deepgram → kokoro → openai`.
3. Place a call (browser **Test call** is fine) and listen: Aura should speak.
4. **Test the backup:** set `VOICE_TTS_PROVIDER=kokoro` temporarily (or set `DEEPGRAM_API_KEY` to a wrong
   value), restart the worker, and call again. You should hear Kokoro's voice. Expect a short pause
   before it speaks on a CPU. Set it back afterwards.

### Good to know
- A business's own voice setting goes first. New businesses default to "elevenlabs", which is
  skipped when `ELEVENLABS_API_KEY` is empty, so they follow your `.env` order.
- Typos are caught early: an unknown name in `VOICE_TTS_FALLBACKS` stops the app at startup with a
  message listing the valid names.
- Deepgram's voice is per language: `aura-2-…-en` voices speak English. Pick another model name for
  other languages Deepgram supports.
- Deepgram bills the voice by characters, and speech-to-text by audio time, from the same balance.
  Watch it in the Deepgram console.

---

# Part 1: Development (free, no Twilio)

## 1.1 Prerequisites

- Accounts and `.env` from **Part 0**, with `scripts.check_env` showing no ❌.
- Indexes created: `uv run python -m migrations.m0002_indexes`.
- Docker Desktop on Windows. The voice worker can't run natively there, so it runs in Docker.
- **Linphone** installed (https://www.linphone.org/ → Download → Windows). Any softphone that can
  place an unregistered SIP call works. MicroSIP (Windows) and Zoiper are alternatives.

## 1.2 Telephony variables

These are the ones that switch on the softphone (all described in Part 0):

```ini
APP_ENV=development
TELEPHONY_PROVIDER=                 # leave empty: development → softphone
SOFTPHONE_TEST_NUMBERS=+15550000001,+15550000002
SOFTPHONE_SIP_USERNAME=15557654321
SOFTPHONE_SIP_PASSWORD=<your generated password>
TRANSFER_PHONE_NUMBER=              # leave empty in dev; see "What each mode can test"
```

Use a real password. The trunk is reachable from the internet, and without credentials anyone who
finds your SIP address could call your agent and run up your OpenAI bill. `setup_telephony` refuses
to run in softphone mode without a username and password.

## 1.3 Decide which business answers

- **Quickest start (no setup):** while the `tenants` collection is empty ("legacy mode"), every
  dialled number resolves to the hotel defined in `.env` (`HOTEL_NAME` etc.). Skip to 1.4.
- **Realistic (recommended):** create a business and route a fake number to it.
  1. `uv run python -m scripts.create_admin --email you@example.com --name "You"`
  2. Start the API (`uv run uvicorn main:app --reload --port 8002`), sign in to the dashboard, and
     create a business on the **Platform** page. (Or use `POST /platform/tenants` from
     http://localhost:8002/docs after `POST /auth/login`.) Note its tenant id.
  3. You'll pass its id as `--assign <tenant_id>` in step 1.4.

Once one business exists, legacy mode is off: a number that isn't assigned to anyone gets the
"unassigned number" message. That's what the second fake number is for.

## 1.4 Create the LiveKit trunk and dispatch rule

```bash
cd AI-receptionist-1
uv run python -m scripts.setup_telephony --dry-run
```

Expected output (the ids and host differ for you):

```
APP_ENV=development  telephony=softphone  (dry run)
  inbound trunk  softphone-inbound-development: would create (numbers ['+15550000001', '+15550000002'])
  dispatch rule  receptionist-development: would create (rooms 'call-…', agent 'receptionist')
```

If it prints "Fix these first", it lists exactly which `.env` values are missing. Then run it for real,
routing the **first** number to your business (the others stay unassigned on purpose):

```bash
uv run python -m scripts.setup_telephony --assign <tenant_id>
# a different number, e.g. to give a second business its own:
uv run python -m scripts.setup_telephony --assign <other_tenant_id> --number +15550000002
```

It prints the Linphone settings to enter. To double-check what exists in LiveKit, run
`lk sip inbound list` and `lk sip dispatch list` (after `lk cloud auth`), or look at **Telephony** in
the LiveKit Cloud dashboard.

Run it again at any time: unchanged objects print "up to date". Change `SOFTPHONE_TEST_NUMBERS` or the
password and re-run to update the trunk.

> **Running a worker that is already up?** It caches tenant lookups for 60 s. Wait a minute after
> `--assign`, or restart it.

## 1.5 Set up Linphone

1. Install and open Linphone. Skip any prompt to create a Linphone account.
2. Add a third-party SIP account (menu names vary by version: **Use a SIP account** /
   **Add account** under Settings → Accounts). Enter:

   | Field | Value |
   |---|---|
   | Username | `SOFTPHONE_SIP_USERNAME` (e.g. `15557654321`) |
   | Password | `SOFTPHONE_SIP_PASSWORD` |
   | Domain / SIP server | the host `setup_telephony` printed, e.g. `<project>.sip.livekit.cloud`. Confirm it against LiveKit Cloud → **Settings → SIP URI** |
   | Transport | **TCP** |
   | Register | **Off** (LiveKit trunks accept calls but not REGISTER. A "registration failed" state is expected and harmless) |

3. Windows Firewall: allow Linphone on private networks if prompted.

## 1.6 Start the worker

```bash
docker compose -f docker-compose.dev.yml up --build agent
```

(Linux/macOS: `uv run python -m agent.worker dev`.) The first log lines include
`Voice agent 'receptionist' (env=development, telephony=softphone, …)`. If it says anything else,
your `.env` isn't what you think.

## 1.7 Place the first call

1. In Linphone, type the SIP address into the search/dial bar and call:
   `sip:+15550000001@<project>.sip.livekit.cloud`
   (If the account's domain is set, dialling just `+15550000001` also works.)
2. If Linphone asks for credentials, it should reuse the account's. Accept any "unregistered
   account" prompt.
3. You should hear the greeting within about a second.
4. In the worker log, confirm:
   - `SIP participant attributes: [...]` lists `sip.phoneNumber` **and** `sip.trunkPhoneNumber`
     (this closes the open check at `agent/worker.py` / Roadmap Task 1.2.c);
   - `Call call-… started for <business id> (phone, caller …)`: the room starts with `call-` and the
     channel is `phone`.

If it doesn't connect, go to [Troubleshooting](#troubleshooting).

## 1.8 Test checklist

Run these in order. "Expected" is what a passing run looks like.

| # | Test | Steps | Expected |
|---|---|---|---|
| 1 | Right business answers | Call `+15550000001` | Greeting names the business you assigned (or the `.env` hotel in legacy mode). Log shows its business id |
| 2 | Caller number seen | Same call, check the log | Caller shows (masked) as `…4321` |
| 3 | A tool works | Ask to book / check availability | Agent calls the tool, speaks the result; the booking or lead appears in the dashboard |
| 4 | Barge-in | Interrupt mid-sentence ("no wait, Thursday") | Agent stops within ~0.3 s and handles the correction |
| 5 | Backchannel | Say "mm-hmm" while it talks | Agent keeps talking |
| 6 | Silence | Say nothing for ~12 s | Agent asks "are you still there?" |
| 7 | Hang-up from your side | End the call in Linphone | Worker log shows the room closing; the call appears in the dashboard Calls page |
| 8 | Hang-up from the agent | Say "thanks, bye" | Agent says goodbye and ends the call |
| 9 | Take a message | Ask for a person while `TRANSFER_PHONE_NUMBER` is empty | Agent explains no one is available and takes a message; the message appears in the dashboard |
| 10 | Unassigned number | Call `+15550000002` (not assigned) | Short neutral message, then hang-up. Log: `Call … to an unassigned number`. Sentry gets a warning if `SENTRY_DSN` is set. *(Needs at least one business, otherwise legacy mode answers.)* |
| 11 | Noisy background | Play music or a podcast near the mic | Agent still understands you (BVCTelephony active on phone calls) |
| 12 | Latency | After ~10 calls: `uv run python -m scripts.latency_report --channel phone` | Compare with the targets in [PHASE_1_TESTING.md](PHASE_1_TESTING.md). Treat dev numbers as a floor; production is the real test |
| 13 | Browser fallback | Dashboard → **Test call** | Same agent answers with no SIP involved. If this works and Linphone doesn't, the problem is SIP, not the agent |

Do **not** test transfers in dev: with a softphone there's no phone network to transfer to. Anything
that depends on a real number (transfer, outbound, caller ID) belongs in Part 2.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Linphone: "not found" / 404 straight away | The number you dialled isn't on the trunk | The number must be in `SOFTPHONE_TEST_NUMBERS`. Re-run `setup_telephony`; the dialled string must be `+1555…` including the `+` |
| 401 / 403, asks for credentials repeatedly | Username or password differ from the trunk | Account username/password must equal `SOFTPHONE_SIP_USERNAME` / `PASSWORD`. Re-run `setup_telephony` after changing `.env` |
| Rings, then nothing / timeout | Transport or firewall | Set Linphone transport to TCP; allow it in Windows Firewall; try another network (some routers/VPNs block SIP) |
| Call connects, you hear nothing | Agent not running, or agent name mismatch | Is the worker up? `LIVEKIT_AGENT_NAME` in `.env` must equal the name in the dispatch rule (re-run `setup_telephony`). Check LiveKit Cloud → Sessions for a `call-…` room |
| Call connects, agent hears nothing / one-way audio | NAT or media ports blocked | Different network, disable VPN. Linphone → Settings → Network: enable ICE |
| Greeting is for the wrong business | Number not assigned, or legacy mode | `setup_telephony --assign <tenant_id>`, then wait 60 s or restart the worker. With zero businesses, the `.env` hotel answers everything |
| "Unassigned number" for the number you assigned | Worker cache, or the number isn't in `phone_numbers` | Wait 60 s or restart; check with `GET /platform/phone-numbers` |
| `setup_telephony`: "Fix these first" | Missing `.env` values | It names each one |
| App won't boot in production: "softphone is not allowed" | `TELEPHONY_PROVIDER=softphone` is set | Clear it. Production always uses Twilio |
| Still stuck at authentication | Linphone and LiveKit disagree about credentials | Last resort, dev only: in the LiveKit Cloud dashboard edit `softphone-inbound-development`, clear its username/password and set **Allowed addresses** to your public IP. Don't leave it open. (Re-running `setup_telephony` puts the credentials back) |

---

# Part 2: Production (and staging) with Twilio

Set `APP_ENV=production` (or `staging`). The provider becomes `twilio` automatically.

## 2.1 In Twilio

1. **Buy a number** (Console → Phone Numbers → Buy a number) with Voice capability.
2. **Elastic SIP Trunking → Trunks → Create** a trunk, e.g. `livekit-prod`.
3. **Origination** (calls coming *in*): add an Origination URI
   `sip:<project>.sip.livekit.cloud;transport=tcp` (LiveKit Cloud → Settings → **SIP URI**).
4. **Termination** (calls going *out*: transfers and the Phase 4 dialer):
   - choose a Termination SIP URI (`<name>.pstn.twilio.com`);
   - create a **Credential List** (username + strong password) and attach it.
5. **Numbers**: add the number to the trunk. A number on a trunk stops using its Voice webhook, so
   the legacy `/voice/incoming` path no longer receives its calls. That's expected. Cut over **one
   number first**.
6. **Enable transfers** (needed by `transfer_to_human`):

   ```bash
   twilio api trunking v1 trunks update --sid TKxxxxxxxx \
     --transfer-mode enable-all --transfer-caller-id from-transferee
   ```

7. **Trust Hub: start now, approval takes days.** Business Profile, then SHAKEN/STIR A-attestation
   and CNAM. Without A-attestation, outbound calls show as "Spam Likely".

## 2.2 Where the Twilio values come from

| Variable | Where to find it |
|---|---|
| `TWILIO_ACCOUNT_SID` | https://console.twilio.com → dashboard → **Account Info** (starts with `AC`) |
| `TWILIO_AUTH_TOKEN` | Same box → **Auth Token** (click to reveal). Also used to verify webhook signatures |
| `TWILIO_PHONE_NUMBER` | Console → **Phone Numbers → Manage → Active numbers** (E.164, e.g. `+15125551234`) |
| `TWILIO_TERMINATION_URI` | Console → **Elastic SIP Trunking → Trunks →** your trunk **→ Termination → Termination SIP URI** (`<name>.pstn.twilio.com`) |
| `TWILIO_SIP_OUTBOUND_USERNAME` / `PASSWORD` | The **Credential List** you created under Termination: you choose the values |
| `TWILIO_SIP_INBOUND_USERNAME` / `PASSWORD` | Only if you set credentials on the origination side; otherwise leave empty (see below) |

Everything else (OpenAI, LiveKit, MongoDB, Deepgram) comes from the same places as in
Part 0, but use **production** projects and keys, not the dev ones. Run
`uv run python -m scripts.check_env` here too: with `APP_ENV=production` it checks Twilio instead of
the softphone.

## 2.3 `.env` (production values, from your secret store, never from git)

```ini
APP_ENV=production
TELEPHONY_PROVIDER=                       # empty → twilio

TWILIO_PHONE_NUMBER=+1...                 # first number; others come from the platform's phone_numbers
TWILIO_TERMINATION_URI=<name>.pstn.twilio.com
TWILIO_SIP_OUTBOUND_USERNAME=...          # the credential list from 2.1 step 4
TWILIO_SIP_OUTBOUND_PASSWORD=...
TWILIO_SIP_INBOUND_USERNAME=              # see the note below
TWILIO_SIP_INBOUND_PASSWORD=

TRANSFER_PHONE_NUMBER=+1...               # staff line (E.164)
LIVEKIT_URL=wss://<prod-project>.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
```

**Inbound credentials.** If Twilio's origination presents credentials, put the same pair in
`TWILIO_SIP_INBOUND_USERNAME/PASSWORD`. If it doesn't (the usual setup), leave them empty and
`setup_telephony` creates a number-only trunk, but it warns that it is unauthenticated. Restrict it
by IP in LiveKit (**Allowed addresses**, using Twilio's published signaling addresses) before real
traffic. Check the current [LiveKit Twilio guide](https://docs.livekit.io/telephony/accepting-calls/inbound-twilio/).

Production also needs everything else in [PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md).
The app refuses to boot with unsafe settings.

## 2.4 Create the LiveKit side

```bash
uv run python -m scripts.setup_telephony --dry-run
uv run python -m scripts.setup_telephony
```

It creates `twilio-inbound-production` (numbers: `TWILIO_PHONE_NUMBER` plus every active Twilio number
in `phone_numbers`), the `receptionist-production` dispatch rule, and `twilio-outbound-production`.
It prints the outbound trunk id:

```
LIVEKIT_SIP_OUTBOUND_TRUNK_ID=ST_xxxxxxxx   ← set this in your env
```

Copy that into your production env and restart the worker.

## 2.5 Give a business a number (automatic)

Turn on automatic numbers once:

```ini
NUMBER_PROVISIONING_ENABLED=true
TWILIO_SIP_TRUNK_SID=TK...                # Twilio → Elastic SIP Trunking → your trunk → Trunk SID
LIVEKIT_SIP_INBOUND_TRUNK_ID=ST_...       # the inbound trunk id setup_telephony printed above
```

Then, in the platform admin, use **New business** (or a business's **Numbers → Add number**) and either
pick a number already in your Twilio account or search and buy one. The order then runs by itself:
attach to the Twilio trunk → add to the LiveKit inbound trunk (through the API) → route to the business
→ test call. Progress shows on the business's Numbers tab, with **Retry** if a step fails. You don't
need to re-run `setup_telephony` for new numbers.

Outside the US and Canada, a number you *buy* waits for a Twilio regulatory bundle (approve it on the
**Numbers** page). A number you already own skips that.

**Manual fallback** (a number you've wired up yourself): Numbers → Add number → **Assign only**, or

```
POST /platform/phone-numbers
{ "e164": "+1...", "tenant_id": "<id>", "provider": "twilio" }
```

then re-run `setup_telephony` **inside the API container** (`docker compose exec api python -m
scripts.setup_telephony`), so it reads the same database as the dashboard.

## 2.6 First real call

1. Start the worker (`systemctl start receptionist-agent`; the log shows `env=production, telephony=twilio`).
2. Call the number from a mobile phone.
3. Check the log line `SIP participant attributes: [...]` for `sip.phoneNumber` and
   `sip.trunkPhoneNumber`. Twilio and LiveKit must both be sending them.
4. Run the whole checklist in 1.8, then also:

| Test | Expected |
|---|---|
| Transfer | Ask for a person: the staff phone rings and the call is handed over (5 out of 5 tries) |
| Outside `TRANSFER_HOURS` | Agent declines and takes a message |
| Landline and a mobile on 4G | Audio is clear both ways |
| Caller ID | The business sees the caller's real number |
| Latency | `scripts.latency_report --channel phone` against the [PHASE_1_TESTING.md](PHASE_1_TESTING.md) targets |

## 2.7 After cutover

Set `LEGACY_TWILIO_VOICE=false` once all numbers are on the SIP trunk.

---

# Part 3: A permanent link to the receptionist (browser)

A link you can keep, open on any device (your phone included) and share, instead of the 15-minute
test links. The agent still runs on **your PC**, so the PC, Docker and the worker must be running
whenever someone uses it.

```
phone / browser ──https──▶ tunnel ──▶ portal (public_call.py) ──▶ LiveKit ◀── worker (your PC)
```

## 3.1 How it stays safe

A public link that anyone can open can start calls, and calls cost money (OpenAI, Deepgram, LiveKit).
So:

- **Only the call page is public.** The tunnel points at `portal`, a tiny app (`public_call.py`) that
  serves just `/talk`, `/call` and `/livekit/token`. The admin API, dashboard login, `/docs` and the
  webhooks are **not in it** (they answer 404 through the tunnel). The full API stays on your machine.
- **The link carries a key** (`?k=…`) made from your `SECRET_KEY`. Without it you can't start a call.
- **Rate limits:** `PUBLIC_CALL_RATE_PER_IP_HOUR` (default 10 calls per visitor per hour) and
  `PUBLIC_CALL_MAX_PER_DAY` (default 100 calls in total). Past that, visitors see "busy, try later".
- **Revocable:** raise `CALL_LINK_VERSION` in `.env` and restart the portal. Every old permanent link
  stops working at once. Print the new one.
- **Not time-limited:** a call lasts until someone hangs up (the agent also hangs up after repeated
  silence). Treat the link like a password: share it only with people you trust.

## 3.2 Start it

```bash
cd AI-receptionist-1
docker compose -f docker-compose.dev.yml up -d agent                              # the voice worker
docker compose -f docker-compose.dev.yml --profile public up -d portal tunnel     # the public page
docker compose -f docker-compose.dev.yml logs tunnel                              # find https://<words>.trycloudflare.com
uv run python -m scripts.call_link --base https://<words>.trycloudflare.com       # prints your permanent link
```

Open the printed link `https://<words>.trycloudflare.com/talk/default?k=…`, press join, allow the
microphone, and talk. On a phone it works the same (HTTPS is what lets the browser use the mic).

- `default` is the business defined in `.env` while no businesses exist. For a real business use its id:
  `--tenant ten_abc123`.
- This is the **Cloudflare quick tunnel**: free, no account. Its address is random and **changes whenever
  the tunnel container restarts** (or the PC reboots). The link's key stays the same, so just print a
  new link with the new address.

## 3.3 A link that never changes (free ngrok account)

For one fixed address, use ngrok. Its free plan gives your account **one permanent address**
(`https://<name>.ngrok-free.dev`, name chosen by ngrok, not editable).

1. Create a free account at https://ngrok.com and copy your **authtoken** (Dashboard → Your Authtoken).
2. Add it to `AI-receptionist-1/.env`: `NGROK_AUTHTOKEN=<token>`
3. Swap the tunnels:
   ```bash
   docker compose -f docker-compose.dev.yml stop tunnel
   docker compose -f docker-compose.dev.yml --profile public-ngrok up -d portal tunnel-ngrok
   docker compose -f docker-compose.dev.yml logs tunnel-ngrok     # shows your permanent address
   uv run python -m scripts.call_link --base https://<name>.ngrok-free.dev
   ```

This path is **not tested** (it needs an ngrok account). ngrok's free addresses may show a one-time
"you are about to visit…" page before the call page; click through. If it doesn't work, the quick
tunnel above is the fallback.

## 3.4 Turn it off

```bash
docker compose -f docker-compose.dev.yml stop tunnel tunnel-ngrok portal   # nothing is public any more
```

To kill a link that was already shared while leaving the portal up: raise `CALL_LINK_VERSION` and
`docker compose -f docker-compose.dev.yml restart portal`.

## 3.5 Later: a real server

When you deploy for real, run the same `public_call:app` on a server behind your own domain (HTTPS) and
use that domain as `--base`. Nothing else changes. Don't expose `main:app` to the internet on the same
port.

---

# Part 4: Test with a real phone (no Twilio): a LiveKit phone number

LiveKit Cloud rents US phone numbers directly, with no SIP trunk and no Twilio account. Per
[LiveKit's docs](https://docs.livekit.io/telephony/start/phone-numbers.md): **US numbers only**, every plan
includes **one free US local number** (the Build plan also lists 50 free inbound minutes), it is
**inbound only** (no outbound calls, no `transfer_to_human` yet), and releasing a number early still bills
the month. Check your own quota in LiveKit Cloud before relying on those figures.

> **Calling a US number from outside the US** costs *your* mobile carrier's international rate. Check it,
> or use a calling app you already pay for. The agent side costs nothing extra.

## 4.1 One-time setup (already prepared for you today)

In `AI-receptionist-1/.env` set:

```ini
TELEPHONY_PROVIDER=livekit
```

then:

```bash
uv run python -m scripts.setup_telephony
```

It creates the dispatch rule `receptionist-development-number` (rooms `call-…`, agent `receptionist`)
and prints the next steps. Running it again is safe ("up to date").

## 4.2 On the day

1. **Start the worker:** `docker compose -f docker-compose.dev.yml up -d agent`
   Check it: `docker compose -f docker-compose.dev.yml logs agent` shows `registered worker`.
2. **Rent the number:** cloud.livekit.io → your project → **Telephony → Phone Numbers → Rent a number**,
   search an area code, **Rent → Confirm rental**.
   (CLI alternative: `lk number search --country-code US --area-code 415`, then `lk number purchase --numbers +14155550100`.)
3. **Assign the dispatch rule:** on that number open **⋮ → Assign dispatch rule →
   `receptionist-development-number` → Save.**
4. **Check your setup:** `uv run python -m scripts.check_env`. You want ✅ for OpenAI, Deepgram, LiveKit and
   `LK number`.
5. **Call the number** from your phone. You should hear the greeting within about a second.
6. **Watch the worker log** while you talk: `docker compose -f docker-compose.dev.yml logs -f agent`.
   Look for:
   - `SIP participant attributes: [...]`: LiveKit doesn't document which attributes it sets for its own
     numbers. If `sip.trunkPhoneNumber` and `sip.phoneNumber` are both listed, great. If not, tell me what
     the log line shows and I'll adapt the code.
   - `Call call-… started for … (phone, caller …)`

With no businesses created yet, the `.env` hotel answers. Once you create businesses, route the number to one:

```bash
uv run python -m scripts.setup_telephony --assign TENANT_ID --number +14155550100
```

(or `POST /platform/phone-numbers` with `"provider": "livekit"`). Until then a number nobody owns plays the
"unassigned" message.

## 4.3 Checklist for the phone test

| Test | Expected |
|---|---|
| Greeting | Heard within about a second, with the right business name |
| Normal turn ("What time is check-in?") | Answer within about 2 seconds (phone latency also depends on your carrier) |
| Interrupt it | The agent stops and handles what you said |
| Silence for ~12 s | "Are you still there?", then it hangs up politely after a second silence |
| Background noise | Still understood (noise cancellation is on for phone calls) |
| Hang up | The call shows in the dashboard Calls page |
| `scripts.latency_report --channel phone` | Real phone-network numbers |

## 4.4 If it doesn't work

| Symptom | Likely cause |
|---|---|
| Busy tone or "number not in service" right after renting | Give it a minute; check the number's status in the dashboard |
| Rings, then silence or hang-up | The dispatch rule isn't assigned to the number (step 3), or the worker isn't running |
| Nothing in the worker log | The call never reached LiveKit: check the number and the rule in **Telephony** in the dashboard |
| "Unassigned number" message | Businesses exist but this number isn't routed to one: use `--assign … --number …` |
| Silent or one-way audio | Tell me which side; the call log shows `roomio_audio` / the participant attributes |

For a number in your own country, or transfers and outbound calls, use Twilio (Part 2) or another SIP
provider; LiveKit numbers are US-only and inbound-only for now.

---

# Switching between modes

**Dev → prod:** change `APP_ENV`, remove `SOFTPHONE_*` and `TELEPHONY_PROVIDER`, fill the Twilio block,
use the production LiveKit project, run `setup_telephony`. Re-run the first-call checks.

**Trying Twilio from a dev machine** (once you have an account): keep `APP_ENV=development`, set
`TELEPHONY_PROVIDER=twilio` and the Twilio variables, and run `setup_telephony`. The trunk names
carry the environment (`twilio-inbound-development`), so they won't collide with production.

**Cleaning up dev:** `lk sip inbound list`, then `lk sip inbound delete <ST_…>` for
`softphone-inbound-development`, and the same for the dispatch rule `receptionist-development`
(`lk sip dispatch list` / `lk sip dispatch delete`).
