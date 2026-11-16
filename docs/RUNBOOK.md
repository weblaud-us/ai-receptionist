# Runbook

What to do when something breaks in production. Each section: **how you notice → check → fix →
afterwards.** Commands run on the server in the deploy checkout unless they say otherwise:

```bash
cd /opt/receptionist && export E=production         # or staging
dc() { docker compose -p receptionist-$E -f deploy/docker-compose.prod.yml "$@"; }
# dc needs the variables deploy.sh sets; for read-only commands this is enough:
set -a; . /etc/receptionist/$E.deploy; TAG=$(git describe --tags); DEPLOY_ENV=$E; ENV_FILE=/etc/receptionist/$E.env; set +a
```

Useful everywhere: `dc ps` · `dc logs --since 15m api-a worker-a jobs` · `curl -s https://API/readyz`
· Grafana "morning dashboard" · Sentry (filter by `tenant_id` / `call_id` tag).

---

## Calls aren't being answered

*Notice:* synthetic-call heartbeat missed, `VoiceWorkerDown`, `ShortCallsSpike`, or a customer says so.

1. **Is it one business or everyone?** One business → step 5. Everyone → continue.
2. Workers up? `dc ps worker-a worker-b` and `dc logs --since 10m worker-a | grep -i -E "error|registered"`.
   A healthy worker logs `registered worker` on start. Restart a dead one: `dc up -d --no-deps worker-a`.
3. LiveKit status page and the LiveKit Cloud dashboard (Agents → workers connected, SIP → calls).
   If LiveKit (or this whole host) is down, Twilio's disaster-recovery URL sends callers to
   `fallback_app.py` on the independent host (rings the staff line). Sentry shows "Telephony fallback used". Nothing else to do but wait and
   post on the status page.
4. Providers: see [A provider is down](#a-provider-is-down).
5. One business: Platform page → its numbers. Is the number assigned (`phone_numbers`)? Is the
   tenant `active`? Outcome `unassigned` in Calls = number not assigned. Outcome `busy` = it hit
   its concurrent-call limit (see [spam storm](#a-spam-call-storm-hits-one-number)).
   Twilio console → the number → is it still on the Elastic SIP trunk?
6. Place a test call: `uv run python -m scripts.synthetic_call --tenant ten_…` (from any machine with
   the env) — tells you within 10 s whether the agent answers for that business.

*Afterwards:* if callers were lost, export the list from Twilio call logs and tell the business.

## Latency spike

*Notice:* `ResponseLatencyHigh` (p95 > 2 s for 10 min) or `GreetingSlow`.

1. Which part? `dc exec api python -m scripts.latency_report --calls 50` splits end-of-turn / LLM first
   token / TTS first byte over the last 50 calls.
2. LLM slow → OpenAI status page. If it is degraded but not failing, the fallback won't kick in on
   its own (it triggers on errors and 6 s timeouts). Temporarily make the fallback primary: set
   `VOICE_LLM_MODEL` to a faster model, or redeploy with `OPENAI_API_KEY` invalid so every call
   uses `VOICE_LLM_FALLBACK_MODEL` (only if `ANTHROPIC_API_KEY` is set).
3. TTS slow → put the next provider first: `VOICE_TTS_PROVIDER=cartesia` (or deepgram), redeploy.
4. Workers overloaded → `docker stats`: CPU near 100 %? Raise `WORKERS` (up to 4) in
   `/etc/receptionist/$E.deploy` and redeploy the current tag, or move to a bigger host. `LIVEKIT_LOAD_THRESHOLD` makes busy workers refuse calls
   so they go to others — check at least one worker has headroom.
5. Mongo slow → Atlas → Performance Advisor / Real-Time. Nothing on the call path waits for Mongo
   except the call-start tenant lookup (cached 60 s).

Settings changes in `/etc/receptionist/$E.env` take effect with `deploy/deploy.sh <current tag> $E`.

## A provider is down

*Notice:* `ProviderErrorRateHigh`, `FallbackProviderInUse`, provider status page.

| Down | What happens on its own | What you do |
|---|---|---|
| OpenAI (LLM) | Calls switch to `VOICE_LLM_FALLBACK_MODEL` within 6 s per turn, if `ANTHROPIC_API_KEY` is set | Nothing if the fallback works. No fallback key → calls fail; set the key now (RUNBOOK → Latency, step 2) |
| Deepgram (STT) | Calls switch to OpenAI speech-to-text (slower) | Nothing; expect higher latency |
| ElevenLabs / primary TTS | Next voice in `VOICE_TTS_FALLBACKS` takes over (sounds different) | If it lasts > 1 h, set `VOICE_TTS_PROVIDER` to the fallback so new calls start on it |
| Twilio | Inbound calls don't reach us at all; SMS fails (reminders retry next tick) | Status page. Customers with forwarded numbers: their carrier's no-answer forward still rings them |
| Resend (e-mail) | Digests/invites fail and are logged | Nothing; digests are claimed per day, so a missed one is not re-sent |
| Meta (WhatsApp) | Replies fail; logged | Nothing |

*Afterwards:* note the duration and whether the fallback held in the incident log.

## A tenant says "calls aren't being answered"

Same as [Calls aren't being answered](#calls-arent-being-answered), step 5 first. Also ask for the
time and the number they called from; search Calls for that time. No record at all = the call
never reached the worker (number/trunk/LiveKit). A record with outcome `busy`, `unassigned`, or a
duration under 10 s tells you which part failed. Open the transcript.

## A deploy went bad

*Notice:* `Api5xxRate`, `ApiDown`, deploy job red, Sentry spike tagged with the new release.

**Rollback = deploy the previous tag:**

- GitHub → Actions → deploy → Run workflow → tag = previous release (`git tag --sort=-v:refname | head`),
  environment = production. Or on the server: `bash deploy/deploy.sh v1.2.2 production`.
- Workers drain one at a time again (up to 15 min each). API rollback is quick.
- A migration that already ran (`m0002_indexes`) is additive and safe to leave.

If the deploy script itself failed half-way, the old containers that weren't replaced are still
running; just run the previous tag.

## Mongo failover / restore

*Notice:* `DependencyDown{name="mongo"}`, `/readyz` → `database: fail`.

- **Failover** (Atlas elects a new primary): automatic, 10–30 s of write errors. Calls in progress
  keep talking (state is in memory); their records are flushed when Mongo is back. Nothing to do.
- **Region / cluster outage:** Atlas status page. New calls fail tenant lookup after the 60 s cache
  expires → callers hear the neutral message. Post on the status page.
- **Restore (data loss or corruption):**
  1. Atlas → Backup → pick the snapshot / point in time → **Restore to a new cluster** (never over
     the live one).
  2. Create a DB user on it; put its URI in a copy of the env file; run a staging deploy against it
     and check `/readyz`, tenant count, last call time.
  3. Switch `MONGODB_URI` in `/etc/receptionist/production.env`, redeploy the current tag.
- **Monthly restore test** (acceptance item): steps 1–2 only, then delete the new cluster.
  Time it. Last test: ____ (date) — ____ minutes.

## Background jobs failing

*Notice:* `DeadJobs`, or reminders/digests not arriving.

```bash
dc exec jobs python -m jobs dead          # what failed, when, and why (no customer data stored)
dc logs --since 1h jobs | grep -i error
dc exec jobs python -m jobs dead --clear  # after fixing the cause
```

Reminders and digests are claimed in the database before sending, so restarting the jobs worker or
running two of them never double-sends. A failed hotel reminder is retried after ~1 hour.

## Outbound dialer

*Notice:* `OutboundOptOutsHigh`, `OutboundCallsFailing`, a complaint, a carrier or Twilio trust
notice, or the answer rate dropping on the Sales dialer → Results page.

**Stop calling first, investigate second.**

```bash
# Fastest: Sales dialer → Campaigns → Pause (new dials stop at once; calls in progress finish).
# Everything at once: set OUTBOUND_DIALER_ENABLED=false in /etc/receptionist/$E.env and redeploy
# the current tag — the tick stops dialling and deploy.sh removes the sales-worker container.
dc logs --since 1h jobs | grep -i "sales dialer"
dc logs --since 1h sales-worker | grep -iE "not dialled|failed|opt"
```

- **Opt-outs high:** listen to the calls (Results → filter "Dnc"). Usually the list (wrong kind of
  business, consumers in it) or a script change. Fix, re-run `tests/evals/test_sales_evals.py`,
  then resume.
- **Calls failing / answer rate collapsed:** check the sales trunk in Twilio (subaccount suspended?
  numbers flagged?) and the SIP status on failed attempts. A spam label: rest the number for a
  week, lower `SALES_MAX_CALLS_PER_NUMBER_PER_DAY`, check attestation and CNAM. Do **not** rotate in
  fresh numbers to dodge labels — carriers punish it.
- **Nothing is being dialled:** Sales dialer page shows why (the warning box). Common: the national
  DNC list is older than 31 days (`python -m scripts.import_dnc_registry --status`), the window is
  closed for the leads' timezones, every caller ID is resting, or the sales worker is down.
- **A complaint about a specific call:** the lead's page shows every compliance check made before
  each call (time, mode, every check), the attempt, transcript and recording. Export what's needed
  for counsel; add the number to the DNC list if it isn't already.

The internal DNC list cannot be edited from the product. If a number must come off (e.g. typed in
by mistake), do it in Mongo by hand, with a note of who asked and when.

## A key leaked

Rotate in this order (ROADMAP Task 0.2), each: create new → update `/etc/receptionist/$E.env` (and the
secret manager) → `deploy/deploy.sh <current tag> $E` → revoke old:

1. Anything that costs money or sends messages: OpenAI, Anthropic, ElevenLabs, Deepgram, Cartesia, Twilio, Meta.
2. LiveKit API key/secret.
3. Atlas DB user password.
4. `SECRET_KEY` (logs everyone out; old call links and calendar links stop working).
5. `TOKEN_ENCRYPTION_KEY`: **prepend** the new key (`NEW,OLD`) and deploy. New secrets use NEW, old
   ones still decrypt with OLD. Stored secrets are not re-encrypted automatically, so OLD stays in
   the list until every calendar is reconnected and every 2FA re-set up (or a re-encryption script
   is written). Removing it early = those calendars and 2FA setups stop working.
6. `METRICS_TOKEN` (also update the scraper's credentials file), webhook secrets (tenants rotate
   their own in Settings → Integrations).

Check provider usage dashboards for activity you don't recognise during the exposure window.
GDPR: if personal data may have been accessed, the 72-hour notification clock starts now — see
[Incident response](#incident-response).

## A customer asks for data export / deletion

- **Export:** there is no export button yet. Use `mongoexport` against Atlas with the query
  `{"tenant_id": "ten_…"}` for each collection (`calls`, `contacts`, `appointments`, `messages`,
  `leads`, `knowledge_items`, `services`). Send as a zip through a link that expires.
- **One caller's data** (a caller, not the business): find the contact by phone in that tenant;
  delete their `contacts` row and unset `transcript` on their `calls`; recordings in the bucket
  under the call ids.
- **Delete a business:** set status `cancelled` (stops calls immediately), wait out the agreed
  retention, then delete every document with that `tenant_id` and its recordings. Release its
  numbers in `phone_numbers` and in Twilio.

Log every request and when it was completed (legal deadline: one month under GDPR).

## A spam call storm hits one number

*Notice:* `BusyLinesRefusingCalls`, one tenant's call count jumps, many short calls from few numbers.

- The per-business limit (`limits.max_concurrent_calls`) already protects everyone else: excess
  calls hear "lines busy" or go to the staff line, and use no LLM.
- Block the callers: Twilio console → the number → or a Twilio trunk-level blocklist for the
  caller IDs in Calls (filter outcome `busy`).
- Many different caller IDs: ask Twilio support; temporarily lower the tenant's limit to 1 so
  costs stay flat.
- Tell the business; they may want calls forwarded straight to staff for an hour.

## Standing up a second region (EU cell)

Only when the first EU customer signs: same images, a second host (Frankfurt/London), its own
`/etc/receptionist/production-eu.env` with an EU Atlas cluster, EU Redis, EU bucket and a LiveKit
project in an EU region, its own `API_DOMAIN`/`APP_DOMAIN`, and a second GitHub environment.
Tenants with `region: eu` live only there. No cross-region replication.

## Incident response

1. **Acknowledge** the alert; post "investigating" on the status page within 15 minutes for anything customer-visible.
2. **Mitigate first** (rollback, fallback, block), investigate after.
3. **Personal data involved?** (leaked key with data access, wrong-tenant data shown, DB exposed)
   → note the time you became aware. GDPR: notify the supervisory authority within 72 hours and
   affected businesses without undue delay; they notify their callers. Keep a written timeline.
4. **Afterwards:** a short write-up — what happened, how long, how found, what changes — in this
   repo under `docs/incidents/YYYY-MM-DD-title.md`.

Contacts: who is on call ________ · Atlas support ________ · LiveKit support ________ · Twilio support ________
