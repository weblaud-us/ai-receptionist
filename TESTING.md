# Testing guide

How to test this repo, from a quick unit-test run to a real phone call. Start here; the detailed
steps for each feature area live in [docs/](docs/) and are linked below.

| Part | Where |
|---|---|
| Backend (FastAPI API, LiveKit voice worker, jobs worker) | [AI-receptionist-1/](AI-receptionist-1/) |
| Client dashboard (businesses) | [dashboard/](dashboard/) |
| Platform admin app | [platform-dashboard/](platform-dashboard/) |
| Shared frontend code | [packages/shared/](packages/shared/) |

## Quick reference

| What | Command | Needs | Cost |
|---|---|---|---|
| Backend tests | `cd AI-receptionist-1 && uv run pytest` | nothing (in-memory Mongo and Redis) | free |
| Backend lint | `uv run ruff check .` | | free |
| Backend types | `uv run mypy` | | free |
| Everything above, on Linux | `make test` (or `docker compose run --rm tests`) | Docker | free |
| Frontend checks | `npm ci && npm run lint && npm run typecheck:shared && npm run build` | Node 22 | free |
| Conversation evals | `RUN_EVALS=1 uv run pytest tests/evals` | a real OpenAI key | a few cents |
| Settings check | `uv run python -m scripts.check_env` | a filled-in `.env` | free (read-only pings) |
| Whole product, local | `make dev` (or `npm run stack`) | Docker, `.env` | free |
| Test phone call | see [docs/TELEPHONY_TESTING.md](docs/TELEPHONY_TESTING.md) | LiveKit dev project, Linphone | free in development |
| Latency report | `uv run python -m scripts.latency_report --channel phone` | calls in the database | free |
| Synthetic call / load test | `uv run python -m scripts.synthetic_call --tenant <id>` | LiveKit, a running worker | LLM + speech usage |

**Before you push:** backend tests, ruff, mypy, and the frontend lint/build must all pass. CI runs
the same checks (see [CI](#ci)). If you changed the prompt, the model or a tool, run the evals too.

## Prerequisites

- **Python 3.12** and [uv](https://docs.astral.sh/uv/). Install exact versions: `cd AI-receptionist-1 && uv sync --frozen`.
- **Node 22+** for the frontends: `npm ci` at the repo root (npm workspaces share one lockfile).
- **Docker** for `make dev` / `make test` and, on Windows, for anything that loads `livekit-agents`.
- **Windows without `make`:** use the `npm run stack*` scripts in [package.json](package.json), or
  run the `docker compose` line from the [Makefile](Makefile) yourself.

## 1. Backend automated tests

```bash
cd AI-receptionist-1
uv run pytest                                   # the full suite, quiet
uv run pytest tests/test_bookings.py -v         # one file
uv run pytest -k "double_booking or overlap"    # by name
uv run pytest -x --lf                           # stop at the first failure, rerun only what failed last time
uv run pytest -rs                               # show why tests were skipped
```

### How the test environment works

- **No real credentials, no network.** [tests/conftest.py](AI-receptionist-1/tests/conftest.py)
  sets every setting before `config` is imported, and those values override your local `.env`.
  Paid integrations (Stripe, Twilio, provisioning, the outbound dialer, signup) are off unless a
  test switches them on.
- **MongoDB** is in memory (`mongomock-motor`), created with the production indexes. Unique
  indexes are enforced, and the double-booking tests depend on that.
- **Redis** is in memory (`fakeredis`) for tests that ask for it.
- **Shared state** (rate limits, dedupe, the metrics cache) is reset before every test.
- **Outside HTTP calls** are mocked with `httpx.MockTransport`. Don't add `respx`.

Fixtures from `conftest.py`:

| Fixture | Gives you |
|---|---|
| `db` | a fresh in-memory database, installed as `get_db()` |
| `redis` | an in-memory Redis, installed as `core.redis.get_redis()` |
| `app` | the FastAPI app without its lifespan (no DB connection, no background tasks) |
| `client` | a `TestClient` for `app` |

### Windows: LiveKit tests skip

On some Windows builds the `livekit-agents` native libraries crash the interpreter on import.
`conftest.livekit_agents_importable()` checks this in a child process, and the LiveKit-dependent
tests (`test_agent_livekit.py`, `test_phase4_sales_agent.py`, parts of phase 6/7) skip
themselves when it fails. To run **everything** on Windows, use Docker:

```bash
make test                                                        # from the repo root
docker compose -f AI-receptionist-1/docker-compose.dev.yml run --rm tests   # same, backend-only compose file
```

A local run that shows a handful of skips is normal. CI on Linux runs every test.

### What the tests cover

| Area | Files |
|---|---|
| Health, config, time zones, logging | `test_health`, `test_config_time_logging`, `test_voice_order` |
| Multi-tenancy and isolation | `test_tenancy`, `test_api_tenancy` (no endpoint or tool leaks across tenants), `test_isolation_guard` (new code reads tenant data only through `core/repositories`) |
| Webhook security | `test_webhook_signatures` (Twilio / Meta signatures, forged requests get 403) |
| Booking and availability | `test_availability`, `test_bookings`, `test_scheduling` |
| Voice agent and tools | `test_agent_logic`, `test_agent_livekit`, `test_tools_registry`, `test_generic_tools`, `test_prompt_builder` |
| Telephony and call links | `test_telephony_config`, `test_livekit_links`, `test_livekit_number_provider`, `test_permanent_link`, `test_admin_calls` |
| Phase 2: integrations, webhooks, summaries | `test_phase2_integrations`, `test_phase2_completion` |
| Phase 3: infrastructure | `test_phase3_infra`, `test_phase3_gaps` (rate limits, dedupe, jobs, metrics, LLM fallback, encryption) |
| Phase 4: outbound dialer | `test_phase4_outbound`, `test_phase4_sales_agent` |
| Phase 5: billing and signup | `test_phase5_billing` |
| Phase 6: compliance, regions | `test_phase6_compliance`, `test_regional_deploy` |
| Phase 7: launch | `test_phase7_launch` |
| Dental vertical | `test_dental_tools`, `test_dental_phase2`, `test_dental_pms`, `test_dental_billing`, `test_dental_privacy`, `test_dental_roi`, `test_patient_reactivation` |

## 2. Lint and type checks

```bash
cd AI-receptionist-1
uv run ruff check .       # real errors only on legacy code; new code in core/ gets the full rule set
uv run mypy               # checks core/ and agent/ (legacy modules are excluded in pyproject.toml)
```

CI also fails if a setting in `config.Settings` is missing from `AI-receptionist-1/.env.example`.
When you add a setting, document it there too.

## 3. Frontends

The dashboards have no unit tests yet. Their checks are lint, the shared package's types, and a
production build (`tsc -b` type-checks each app as part of the build):

```bash
npm ci
npm run lint                # every workspace
npm run typecheck:shared    # packages/shared
npm run build               # dashboard + platform-dashboard
```

CI also checks that the client bundle has no platform-admin code in it:

```bash
grep -lE '/platform/(tenants|sales|costs)' dashboard/dist/assets/*.js   # must print nothing
```

To click through the UI: `make dev`, then open the dashboard at http://localhost:5173 and the
platform app at http://localhost:5174 (see [section 5](#5-running-the-whole-product-locally)).

## 4. Conversation evals (real LLM)

[AI-receptionist-1/tests/evals/](AI-receptionist-1/tests/evals/) runs scripted conversations
against the real model and checks which tools it calls and what it says. Without `RUN_EVALS=1`
they skip, but `test_case_files_are_valid` still runs, so a malformed case file fails CI for free.

```bash
cd AI-receptionist-1
RUN_EVALS=1 uv run pytest tests/evals -rs                          # uses EVAL_OPENAI_API_KEY, or OPENAI_API_KEY from .env
RUN_EVALS=1 uv run pytest tests/evals/test_case_evals.py -k dental # one group
RUN_EVALS=1 EVAL_LLM=fallback EVAL_ANTHROPIC_API_KEY=sk-ant-... uv run pytest tests/evals   # the fallback LLM
```

On Windows, run them in Docker:
`docker compose -f docker-compose.dev.yml run --rm -e RUN_EVALS=1 tests pytest tests/evals`.

| File | Covers |
|---|---|
| `test_voice_evals.py` | core receptionist behaviour (booking, transfer, unknown questions, prompt injection, …) |
| `test_sales_evals.py` | the outbound sales agent |
| `test_case_evals.py` | one replay per JSON file in `tests/evals/cases/` |

**When to run them:** after any change to the prompt, the model (`VOICE_LLM_MODEL`), a tool's
description or a vertical template, and before every release. All must pass. The fallback LLM must
pass the same evals before it carries real calls.

**Adding a case:** a call went wrong in a pilot → in the platform app, open Transcript review →
Make eval case → Approve, then run `uv run python -m scripts.export_eval_cases`. You can also write
the JSON by hand. The format and rules (no personal data, one behaviour per case) are in
[tests/evals/cases/README.md](AI-receptionist-1/tests/evals/cases/README.md).

## 5. Running the whole product locally

```bash
cp AI-receptionist-1/.env.example AI-receptionist-1/.env   # then fill it in
make dev                                                   # or: npm run stack
```

| Service | URL |
|---|---|
| API | http://localhost:8002 (Swagger at `/docs`, metrics at `/metrics`) |
| Client dashboard | http://localhost:5173 |
| Platform admin app | http://localhost:5174 |
| MongoDB / Redis | `localhost:27017` / `localhost:6379` |

Code is mounted, so the API, the worker and both frontends reload when you save a file. The outbound
sales worker starts with the stack too. Nothing is dialled unless `OUTBOUND_DIALER_ENABLED=true` is set
and a campaign is started or a single call is approved.

**Smoke checks** once it's up:

```bash
curl -s localhost:8002/readyz                        # {"checks":{"database":"ok","redis":"ok"}}
curl -s localhost:8002/metrics | grep receptionist_  # no token needed in development
docker compose exec jobs python -m jobs dead         # expect: 0 dead job(s)
docker compose logs jobs | grep "Jobs worker started"
```

**Check your keys** before you place a call: `cd AI-receptionist-1 && uv run python -m scripts.check_env`.
It's read-only, never prints a secret, and reports OK / WARN / FAIL for each setting.

## 6. Manual end-to-end testing

### Voice calls

| Method | How | Guide |
|---|---|---|
| Browser call (no phone number) | `uv run python -m scripts.call_link --base http://localhost:8002` and open the link | [PHASE_1_TESTING.md](docs/PHASE_1_TESTING.md) |
| Softphone over SIP (free, no Twilio) | `APP_ENV=development`, `uv run python -m scripts.setup_telephony`, call from Linphone | [TELEPHONY_TESTING.md](docs/TELEPHONY_TESTING.md) |
| Real phone number (staging) | Twilio Elastic SIP trunk → LiveKit | [TELEPHONY_TESTING.md](docs/TELEPHONY_TESTING.md) |

On the first SIP call, check that the worker log line `SIP participant attributes: [...]` includes
`sip.phoneNumber` and `sip.trunkPhoneNumber`.

**Call script.** Before a release, run the 20-call script in
[PHASE_1_TESTING.md](docs/PHASE_1_TESTING.md#acceptance-checks-task-112c). It covers booking, a
taken slot, "are you a robot?", transfer in and out of hours, an unknown question, prompt injection,
barge-in, long silence, a noisy line and a strong accent.

### Latency and load

```bash
uv run python -m scripts.latency_report --calls 50 --channel phone   # p50 / p95 against the targets
uv run python -m scripts.synthetic_call --tenant ten_abc123          # one call; exit 1 if no greeting in 10 s
uv run python -m scripts.synthetic_call --tenant ten_abc123 --concurrency 20 --ramp 30 --hold 60   # load test
```

Targets: response p50 ≤ 1.0 s, p95 ≤ 1.8 s, tool turns ≤ 1.6 s, greeting ≤ 0.7 s. If a change
misses them, the report's "where the time goes" section shows which stage to tune.

### Signup and billing

- **Without Stripe or Twilio:** set `SIGNUP_ENABLED=true`, `NUMBER_PROVISIONING_ENABLED=true`,
  `TELEPHONY_PROVIDER=softphone`, then sign up at `/signup`. You get fake `+1555…` numbers, the
  verification e-mail is printed in the API log, and the trial is local.
- **With Stripe test mode:** forward webhooks with `stripe listen --forward-to localhost:8002/billing/stripe/webhook`
  and pay with card `4242 4242 4242 4242`.

Both are covered step by step in [PHASE_5_TESTING.md](docs/PHASE_5_TESTING.md).

### Feature-area guides

Each guide lists what was built, how to try it locally, and the acceptance checks for that area.

| Guide | Covers |
|---|---|
| [PHASE_1_TESTING.md](docs/PHASE_1_TESTING.md) | voice agent, latency, call script |
| [PHASE_2_TESTING.md](docs/PHASE_2_TESTING.md) | multi-tenant core, dashboard, integrations |
| [PHASE_3_TESTING.md](docs/PHASE_3_TESTING.md) | Redis, jobs, metrics, LLM fallback, rate limits |
| [PHASE_4_TESTING.md](docs/PHASE_4_TESTING.md) | outbound sales dialer (staging first) |
| [PHASE_5_TESTING.md](docs/PHASE_5_TESTING.md) | billing, signup, number provisioning |
| [PHASE_6_TESTING.md](docs/PHASE_6_TESTING.md) | legal and compliance, data export and erasure |
| [PHASE_7_TESTING.md](docs/PHASE_7_TESTING.md) | pilot and launch |
| [TELEPHONY_TESTING.md](docs/TELEPHONY_TESTING.md) | accounts, `.env`, softphone and Twilio calls, voice providers |
| [LAUNCH_CHECKLIST.md](docs/LAUNCH_CHECKLIST.md), [PRODUCTION_CHECKLIST.md](docs/PRODUCTION_CHECKLIST.md) | before going live |

## CI

[.github/workflows/ci.yml](.github/workflows/ci.yml) runs on every PR and every push to `main`.
Release tags run the same checks before deploying.

| Job | Runs | Locally |
|---|---|---|
| `backend` | `ruff`, `mypy`, `pytest`, `.env.example` completeness | `make test` |
| `evals` | evals on the main LLM and the fallback LLM (only when the `EVAL_*` secrets are set) | section 4 |
| `frontends` | lint, shared typecheck, build, client-bundle check | section 3 |
| `security` | `pip-audit`, `npm audit --omit=dev --audit-level=high`, gitleaks | see the workflow |
| `containers` | builds every image, checks they run as non-root and import cleanly, validates the compose files | `make images` |

`uv sync --frozen` and `npm ci` mean CI uses exactly the lockfiles. If you change a dependency,
commit the updated `uv.lock` or `package-lock.json` along with it.

## Writing new tests

- Put backend tests in `AI-receptionist-1/tests/test_<area>.py`. Async tests need no decorator
  (`asyncio_mode = "auto"`).
- Use the `db`, `redis` and `client` fixtures. Don't connect to a real service.
- Read and write tenant data through `Repos(db, tenant_id)`. `test_isolation_guard.py` fails if new
  code under `core/`, `routers/`, `agent/`, `services/` or `jobs/` touches collections directly.
- Any new endpoint or tool that returns tenant data needs a cross-tenant case in `test_api_tenancy.py`.
- Integrations stay off by default in `conftest.py`. Turn one on inside the test with `monkeypatch.setattr(settings, ...)`.
- Mock outside HTTP with `httpx.MockTransport`.
- A test that imports `livekit.agents` must skip when `livekit_agents_importable()` is False, or it
  will crash pytest on Windows.
- A bug in a conversation (wrong tool, made-up price, missed emergency) belongs in an eval case, not
  a unit test.

## Troubleshooting

| Symptom | Fix |
|---|---|
| pytest crashes or hangs on import on Windows | `livekit-agents` native libraries; run `make test` in Docker |
| Tests skip locally but run in CI | expected on Windows (LiveKit); `pytest -rs` shows the reasons |
| Evals all skip | set `RUN_EVALS=1` and a real key (`EVAL_OPENAI_API_KEY` or `OPENAI_API_KEY` in `.env`) |
| A test passes alone but fails in the full suite | shared state leaking; check that the test uses the fixtures instead of module-level globals |
| CI says `.env.example is missing: X` | add `X=` (or `# X=`) to `AI-receptionist-1/.env.example` |
| `/readyz` reports redis or database down under `make dev` | `docker compose ps`; Mongo and Redis must be healthy before the API starts |
| Frontend hot reload doesn't fire on Windows | already handled with polling (`DEV_WATCH_POLLING`); restart the container if it stalls |
| Test call connects but the agent is silent | run `scripts.check_env`, then check the worker logs: `docker compose logs -f worker` |
