# Phase 4 — outbound AI sales dialer: what's built and how to test it

What Phase 4 added (ROADMAP.md §9), how to run it safely, and how to check each acceptance item.
Production steps are in [PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md#phase-4--outbound-sales-dialer);
what to do when something goes wrong is in [RUNBOOK.md](RUNBOOK.md#outbound-dialer).

> ⚖ **Nothing here is legal advice.** The rules in `core/outbound/rules.py` are a conservative
> reading of ROADMAP §9 / §11 so the dialer fails closed. A telemarketing lawyer must review the
> call type, the opener and the lead list for every market before the first real call. The
> dashboard will not start a campaign until someone confirms that review (it is recorded).

## Safe by default

- `OUTBOUND_DIALER_ENABLED=false` by default. While it is false the dialer tick only tidies up;
  nothing is ever dialled, whatever the dashboard says.
- Even when it is on, a campaign is a **draft** until started, and starting needs the legal-review
  confirmation.
- In production the app refuses to boot with the dialer on unless sales calls have **their own
  trunk** (`SALES_SIP_OUTBOUND_TRUNK_ID`, different from the customers' trunk), a sales tenant, and
  Twilio credentials for line-type lookups.

## What's in the code

| Roadmap task | Where |
|---|---|
| 4.0 Legal design | `core/outbound/rules.py`: which call type may dial which country (`ai_cold_b2b_us` → US only; `ai_consented` / `human_assisted` → `SALES_AI_CONSENTED_COUNTRIES` / `SALES_HUMAN_ASSISTED_COUNTRIES`), legal calling windows per country and the US states that are narrower than federal 8 am–9 pm, required do-not-call lists per country. States with mini-TCPA laws are switched off for AI cold calls (`SALES_BLOCKED_US_STATES`, default FL, OK, MD, WA) |
| 4.1 Numbers and reputation | Campaign caller IDs with local presence (same area code, else same state); `SALES_MAX_CALLS_PER_NUMBER_PER_DAY` (30) rests a number for the day; separate sales trunk enforced in production; `sales-worker` is its own container so the dialer can't take capacity from inbound calls. STIR/SHAKEN, CNAM and carrier registration are account steps (checklist) |
| 4.2 Data model | `sales_leads` (the tenant `leads` collection already holds receptionist job requests), `campaigns`, `call_attempts`, `dnc_list`, `consent_records`, plus `dnc_registry` / `dnc_registry_meta` for downloaded lists. Indexes in `database/mongodb.py` (run by `m0002_indexes`) |
| 4.3 Intake + compliance gate | `core/outbound/importer.py` (CSV → E.164 with `phonenumbers`, de-dupe against the file, existing leads and **existing customers**, timezone and state from the number, `source` on every row, pre-screen). `core/outbound/gate.py`: internal DNC, market/state, line type (Twilio Lookup, `core/outbound/lookup.py`, cached `SALES_LINE_TYPE_MAX_AGE_DAYS`), national/state registries (must be loaded and < 31 days old for the number's area code), consent (written, unrevoked), frequency, calling window in **every** timezone the lead might be in. Anything unknown fails. Every decision is stored on the lead (`gate`, `gate_log`) and on the attempt. Registries load with `scripts/import_dnc_registry.py` |
| 4.4 Dialer engine | `core/outbound/dialer.py`: `tick()` every 20 s (`sales_dialer_tick` in the jobs worker; in-process without Redis): reap dead attempts, then per running campaign claim leads atomically, gate again, pick a caller ID, write the attempt, dispatch the sales agent (`create_dispatch`, room `out-<attempt>`). Concurrency, daily cap and a 10-dials-per-tick ceiling. `agent/sales.py` + `agent/sales_worker.py`: gate a third time, AMD (`livekit.agents.AMD`) around `create_sip_participant` (ring timeout, max call duration), machine → hang up (or the consented voicemail), person → the fixed opener. Outcomes in `core/outbound/outcomes.py` (retry delays, max attempts, callbacks, exactly-once finish) |
| 4.5 Sales persona | `core/outbound/persona.py`: prompt per ROADMAP, two script versions for A/B, opener spoken **from a template** (always "AI", company, opt-out). Tools in `core/outbound/sales_tools.py`: `check_demo_slots` / `book_demo` (Phase 2 scheduling engine on `SALES_TENANT_ID`'s "Demo" service and calendar), `send_info_email` (e-mail only), `schedule_callback`, `add_to_dnc`, `mark_wrong_number`, `transfer_to_founder`, `end_call`. Safety in code: `core/outbound/optout.py` checks every callee turn before the model answers → DNC + fixed goodbye + hang-up; a 10 s hang-up timer after any opt-out; max call duration. Evals: `tests/evals/test_sales_evals.py` |
| 4.6 Dashboard | **Sales dialer** page (platform admins, under Platform): Campaigns (builder, legal sign-off, start/pause, progress), Leads (CSV import with column mapping and preview, search/filter, gate results and history per lead, "check now", manual DNC), Live calls (listen in as a hidden participant, take over — the agent goes silent for the rest of the call —, end call, "Dial for me" for human-assisted campaigns), Results (funnel, cost per demo, script A/B, AMD accuracy, every attempt with transcript, summary, recording, outcome correction and AMD labelling), Compliance (registries loaded, what may be called, the internal DNC list). API: `routers/sales.py` |
| 4.6 Consent funnel | `GET /public/demo-call/consent` (exact wording + version) and `POST /public/demo-call`: written consent record (text shown, IP, user agent, page), lead joins the running `ai_consented` campaign `SALES_CONSENT_CAMPAIGN_ID`. Honeypot field, per-IP and daily limits, refuses numbers on the DNC list |
| Observability | `/metrics`: `receptionist_outbound_attempts_last_hour{outcome}`, `receptionist_outbound_calls_active`; alerts `OutboundOptOutsHigh`, `OutboundCallsFailing`. Sales calls are excluded from the short-call alert (voicemail hang-ups are short by design) |

**Not built, deliberately:**
- **No override.** There is no endpoint or button that skips the gate, removes a DNC entry or
  dials an arbitrary number (a test checks the route table).
- **Task 4.7 (outbound for customers)** — the data model has `owner: "platform"` so it can be
  added; nothing else exists yet. It needs an Acceptable Use Policy first.
- **Trial / paid conversion** in the funnel — those come from billing (Phase 5); the API returns `null`.
- **Branded calling** and carrier-registration automation — provider/account steps, not code.
- **State do-not-call lists** are checked when loaded (`us_state:XX`) but not required; mini-TCPA
  states are switched off instead until counsel says otherwise.

## Setting it up (staging first)

1. **Your business as a tenant.** The New business form only makes AU/UK dental practices, so use
   the script: `uv run python -m scripts.seed_sales_tenant --timezone Asia/Dhaka` (options:
   `--open/--close/--days` = meeting hours, `--owner-email` = "meeting booked" alerts,
   `--transfer` = the number for `transfer_to_founder`). It creates `ten_sales_weblaud` (vertical
   "professional") with a 15-minute service named `Demo` (`SALES_DEMO_SERVICE`); re-run it to
   change those. Optionally connect Google Calendar on its Platform page. Put
   `SALES_TENANT_ID=ten_sales_weblaud` in `.env` and restart.
2. **A sales trunk.** A separate Twilio subaccount with its own Elastic SIP trunk and numbers;
   `lk sip outbound create` for it → `SALES_SIP_OUTBOUND_TRUNK_ID`. (Outside production the
   customers' `LIVEKIT_SIP_OUTBOUND_TRUNK_ID` is used if this is empty.)
3. **Line-type lookups:** `TWILIO_LOOKUP_ACCOUNT_SID` / `TWILIO_LOOKUP_AUTH_TOKEN` (or the main
   Twilio credentials).
4. **Do-not-call lists:** register as a seller (SAN) at telemarketing.donotcall.gov, download the
   area codes you'll call, then
   `uv run python -m scripts.import_dnc_registry us_national 512.txt 737.txt` — and monthly after
   that. `--status` shows what's loaded.
5. `OUTBOUND_DIALER_ENABLED=true`, restart; run the sales worker
   (`docker compose up` starts it locally, or `python -m agent.sales_worker dev`; production:
   `deploy.sh` starts `sales-worker` when the flag is on). The Sales dialer page's warning box
   disappears when everything is in place.

## Local testing

```bash
cd AI-receptionist-1
uv run pytest tests/test_phase4_outbound.py -v          # gate, import, dialer, outcomes, API, consent form
docker compose -f docker-compose.dev.yml run --rm tests pytest tests/test_phase4_sales_agent.py -v   # Linux: needs livekit-agents
RUN_EVALS=1 uv run pytest tests/evals/test_sales_evals.py   # real LLM, a few cents; after every prompt change
```

No test dials anything: dispatch is a fake, Twilio Lookup is stubbed, Mongo is in memory.

**A first real call, to yourself:** import a one-row CSV with your own **landline** (or a test
line you control), create a campaign with a window that is open now and one caller ID, start it,
and watch Live calls. Listen in, then take over. Then say "please don't call me again" on a second
lead and check the number lands on the DNC list (Compliance tab) and is never dialled again.

## Acceptance checks (ROADMAP §9)

| Check | How |
|---|---|
| A lawyer has reviewed the US `ai_cold_b2b_us` mode, the opener and the gate | Outside the code. Give them `core/outbound/rules.py`, `core/outbound/gate.py`, the openers (Campaign dialog shows them) and `routers/sales.py` `CONSENT_TEXT`. Their sign-off is what you confirm when starting each campaign |
| Gate blocks mobile numbers, internal DNC, national DNC, outside hours — automated tests | `tests/test_phase4_outbound.py`: `test_gate_blocks_mobile_and_unknown_lines`, `test_gate_blocks_the_internal_dnc_list`, `test_gate_blocks_the_national_dnc_registry`, `test_gate_refuses_to_dial_without_a_fresh_registry`, `test_gate_blocks_calls_outside_hours`, `test_tick_never_dials_a_mobile` ✅ |
| "Please don't call me" → DNC written, call ends, number never dialled again (test) | `test_please_dont_call_me_means_never_dialled_again`, `test_opt_out_phrases_are_caught`, and on Linux `test_opt_out_is_caught_in_code_before_the_model_answers` (DNC row, fixed goodbye, hang-up + 10 s fallback timer) ✅. Then once on a real call (above) |
| AMD correctly classifies ≥ 90% of a 100-call hand-labelled sample | Results → open each call → "Who really answered?". The Results card shows accuracy and how many real people were hung up on. Tune `DetectionOptions` in `agent/sales.py` if it is under 90% |
| 50 real calls made: answer rate, conversation rate and demos booked recorded | Results → Funnel (dialled → connected → person → talked > 30 s → interested → demo), answer rate, demo rate, cost per demo |
| Outbound numbers show A-attestation and CNAM; no spam label after week 1 | Account steps (checklist). Watch the answer rate daily; a sudden drop = a new spam label |

## The agent that learns from its calls (SALES_LEARNING_PLAN.md)

Automated: `tests/test_sales_learning_stats.py`, `test_sales_memory.py`, `test_sales_coach.py`, `test_sales_playbook.py`,
`test_sales_experiments.py`, `test_sales_learn.py`, `test_sales_timing.py`, `test_sales_bandit.py`, `test_learning_demo.py`.

See it working without weeks of calls:
1. `uv run python -m migrations.m0006_sales_offers --apply` and `uv run python -m migrations.m0008_sales_learning --apply`.
2. `uv run python -m scripts.seed_learning_demo --apply` → Sales dialer → Offers → WebLaud → **Learning**: playbook v1 live,
   v2 rolled back (with why), v3 being tested on a third of calls; the learning curve; Reports → "When businesses pick up".
3. `uv run python -m scripts.seed_learning_demo --apply --fast-forward 30` prints each simulated day until the test decides.
4. `uv run python -m scripts.learning_replay --offer off_weblaud_studio` replays the planned checks over the calls (read-only).
5. `uv run python -m scripts.seed_learning_demo --remove` deletes only the demo data.

Manual checks on a real setup:
- A lead with an earlier "call me Tuesday" call: Leads → the lead → "What the agent remembers"; the offer's Full prompt
  preview for that lead shows `<earlier_calls>`. Forget empties it (audited).
- After an AI call: Results → the call → **AI coach** (scores, moments, lessons, flags) within about a minute.
- Offer → Learning → Edit lessons: a lesson with a price or a figure is refused; a valid one runs its test calls (Linux
  jobs worker), then goes live or into a live test. Without a Linux worker it ends in "Failed its test calls" and calls
  keep the baseline.
- Start a campaign: the legal sign-off dialog has "Let the agent use lessons from earlier calls". Untick it → new calls
  record `arm: off`.
