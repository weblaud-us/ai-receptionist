# Phase 3 — production infrastructure: what's built and how to test it

What Phase 3 added (ROADMAP.md §8), how to run it, and how to check each acceptance item.
Production steps are in [PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md); what to do when
something breaks is in [RUNBOOK.md](RUNBOOK.md).

## What's in the code

| Roadmap task | Where |
|---|---|
| 3.1 Hosting layout | [deploy/docker-compose.prod.yml](../deploy/docker-compose.prod.yml): Caddy (TLS) → 2 API replicas (1 while `LEGACY_TWILIO_VOICE` is on, enforced by `deploy.sh`) + dashboard; 2–4 voice workers (`WORKERS`); jobs worker; Redis. MongoDB is Atlas, media/SIP is LiveKit Cloud. `fallback_app.py` on a second host for Twilio's disaster-recovery URL |
| 3.2 Regional cells | `tenant.region` exists (Phase 2). One cell (US) for now; an EU cell is the same stack with its own env file, Atlas cluster, Redis and LiveKit project (RUNBOOK → "Standing up a second region") |
| 3.3 Containers + local dev | [AI-receptionist-1/Dockerfile](../AI-receptionist-1/Dockerfile) targets `api`, `worker`, `jobs`, `dev` (non-root, health checks, model files downloaded at build); [dashboard/Dockerfile](../dashboard/Dockerfile) (Nginx + security headers); [docker-compose.yml](../docker-compose.yml) + [Makefile](../Makefile): `make dev` starts everything |
| 3.4 State in Redis + job queue | `core/redis.py`, `core/ratelimit.py` (login, public call link, `/auth/*`, per-business API limits), `core/dedupe.py` (WhatsApp retries), `jobs/` (arq: tenant jobs, hotel reminders, WhatsApp replies; backoff + dead-letter list). Hotel reminders are now claimed atomically (`services/reminders.py`). Tenant settings cache: a Redis generation counter clears every process's cache within ~1 s of a save (`core/tenancy/resolver.py`). Pre-rendered greetings shared across workers through Redis (`agent/greeting.py`). Without `REDIS_URL` everything falls back to process memory and the API runs the jobs, as before |
| 3.5 Fallbacks + timeouts | `agent/session_factory.py`: LLM → `VOICE_LLM_FALLBACK_MODEL` (Anthropic) after 6 s; Deepgram STT → OpenAI STT; TTS chain from Phase 2. OpenAI client: connect 2 s / total 15 s. Twilio trunk disaster-recovery URL → `POST /telephony/fallback` (`routers/telephony.py`) rings the staff line or apologises; served by `fallback_app.py` on an independent host (`TELEPHONY_FALLBACK_ONLY=true`). Evals can run on the fallback LLM alone (`EVAL_LLM=fallback`) |
| 3.6 Capacity + drain | Per-business limit editable on the Platform page ("Max calls at once"). `agent/worker.py`: `LIVEKIT_DRAIN_TIMEOUT` (900 s), `LIVEKIT_LOAD_THRESHOLD`, per-call memory warn/kill, per-business concurrent-call limit (`core/concurrency.py`, Redis) → `BusyLineAgent` transfers or says the lines are busy (outcome `busy`). `stop_grace_period: 16m` on workers |
| 3.7 Observability | `GET /metrics` (`core/metrics.py`, Bearer `METRICS_TOKEN`): HTTP metrics + call outcomes, latency p50/p95, greeting time, provider errors/fallbacks (recorded per call by `agent/health.py`), transfers, dependency health, dead jobs. Alerts [deploy/monitoring/alerts.yml](../deploy/monitoring/alerts.yml), dashboard [deploy/monitoring/dashboard.md](../deploy/monitoring/dashboard.md). `/readyz` checks Redis too. Sentry tagged with `tenant_id` / `call_id` (ids only) |
| 3.7 Synthetic calls | `scripts/synthetic_call.py`: a real call through LiveKit; exit 1 if no greeting in 10 s; `--heartbeat` pings your uptime tool on success; `--dial +1…` calls the real number through the outbound SIP trunk (carrier → Twilio → LiveKit → worker) |
| 3.8 Security hardening | Guest passport/ID numbers encrypted at rest and only returned masked (`core/sensitive.py`, migration `m0004_encrypt_guest_ids`, run by every deploy). Per-IP limit on sign-in/reset/invite endpoints (not token refresh), per-business API limit, security headers on API + dashboard (CSP, HSTS, X-Frame-Options), CI `pip-audit`, `npm audit`, `gitleaks`, Dependabot. Provider-account steps (MFA, spend caps, Atlas allowlist) are in the checklist |
| 3.9 Load test | `scripts/synthetic_call.py --concurrency 50 --ramp 60 --say question.wav` |
| 3.10 Runbook | [docs/RUNBOOK.md](RUNBOOK.md) |
| CI/CD | `.github/workflows/ci.yml` (+ security, image builds, compose validation); `deploy.yml` + `deploy-environment.yml`: tag `v*-rc.*` → staging, tag `v*` → staging then production; [deploy/deploy.sh](../deploy/deploy.sh) rolls one replica at a time |

**Not built, deliberately:**
- **Autoscaling.** The single-host stack takes 2–4 workers (`WORKERS`); adding workers on demand
  needs a platform (LiveKit Cloud Agents, ECS, Fly). The worker image is ready for it (deploy/README.md → Capacity).
- **A global region directory** — only one cell exists.
- **Twilio-balance and LLM-spend alerts** — set budget alerts in each provider's console; they see the bill, we don't.
- **OpenTelemetry ≥ 1.45** — blocked by the livekit-agents 1.8.6 pin; CI ignores those two CVEs
  with the reason written in `ci.yml`. Fixed by the planned livekit-agents/openai v3 upgrade.

## Local testing

```bash
make dev                      # or: docker compose up --build   (API, worker, jobs, dashboard, Mongo, Redis)
make test                     # lint + mypy + tests on Linux (includes the LiveKit tests)

cd AI-receptionist-1
uv run pytest tests/test_phase3_infra.py tests/test_phase3_gaps.py -v   # limits, dedupe, jobs, metrics, fallback, ID encryption, shared caches
```

Tests use an in-memory Redis (`fakeredis`); nothing needs a real Redis or network.

Quick manual checks against `make dev`:

```bash
curl -s localhost:8002/readyz                       # {"checks":{"database":"ok","redis":"ok"}}
curl -s localhost:8002/metrics | grep receptionist_ # open without a token in development
docker compose exec jobs python -m jobs dead        # 0 dead job(s)
docker compose logs jobs | grep "Jobs worker started"
```

## Acceptance checks (ROADMAP §8)

| Check | How |
|---|---|
| Staging and production, deployed by CI from git tags | `git tag v0.3.0-rc.1 && git push origin v0.3.0-rc.1` → Actions → deploy: build, then *staging* green. `git tag v0.3.0 && git push origin v0.3.0` → staging, then production (after your approval if the environment requires a reviewer). `curl https://api…/readyz` on both |
| Deploy during 5 live test calls → 0 dropped | Start 5 calls that last ~5 min (`python -m scripts.synthetic_call --tenant ten_… --concurrency 5 --hold 300`, plus one real phone call), then push a new tag. All calls run to the end (`ok: 5`); `docker compose logs worker-a` shows "draining". Calls placed during the deploy are answered by the other worker |
| Kill a worker mid-call → the next call is answered within 1 s | `docker kill receptionist-production-worker-a-1` during a call: that call drops (expected). Immediately run `synthetic_call` → `greeting` ≤ the usual value, answered by worker-b (`docker compose logs worker-b`). Restart: `deploy/deploy.sh <current tag> production` |
| Disable the primary TTS key → calls continue on the fallback TTS | In staging set `ELEVENLABS_API_KEY` to a revoked key (not empty: empty skips it), redeploy, call. The call works in the next voice; the call record has `fallbacks: {"tts": 1}` and the `FallbackProviderInUse` alert fires after 10 min. Same for the LLM: invalid `OPENAI_API_KEY` with `ANTHROPIC_API_KEY` set |
| 50 concurrent calls with p95 response latency ≤ 1.8 s | Raise the test tenant's `limits.max_concurrent_calls` to 60 and ask providers for higher rate limits first. `synthetic_call --concurrency 50 --ramp 60 --hold 120 --say question.wav`, then `python -m scripts.latency_report --calls 50` and the `receptionist_turn_latency_seconds` panel. Watch worker CPU/RAM (`docker stats`) and Atlas connections |
| Synthetic call + alerts reach your phone when you break something | Cron the synthetic call every 15 min with `--heartbeat`; stop both workers (`docker compose stop worker-a worker-b`) → heartbeat alert within ~35 min; `ApiDown` / `DependencyDown` fire within 2 min if you stop the API or Redis. Restart everything afterwards |
| Atlas restore test done and timed | RUNBOOK → "Mongo failover / restore": restore last night's snapshot to a new cluster, point a staging env file at it, `/readyz` green, count tenants/calls. Write down the minutes it took in the runbook |

## Things to know

- **`REDIS_URL` is required in production** (the app refuses to boot without it). In development it
  is optional; without it limits are per process and the API runs the background jobs.
- **Redis down = degraded, not broken.** Rate limits and dedupe fall back to process memory, the
  per-business call limit stops refusing calls, the job queue pauses (the API answers WhatsApp in
  process). `/readyz` reports `redis: fail` so the alert fires.
- **The legacy Twilio Media Streams path** (`LEGACY_TWILIO_VOICE=true`) keeps call state in one
  API process. Run a single API replica until numbers are on the SIP trunk and the flag is off.
- **A deploy can take ~30 minutes**: each voice worker waits up to `LIVEKIT_DRAIN_TIMEOUT` for its
  longest call. That is the point; don't shorten it to make deploys faster.
- **`/metrics` call numbers are global**, computed from the `calls` collection every 15 s, so scrape
  one address, not each replica.
- ⚠ **Verify on first use:** `synthetic_call.py` was written against the installed LiveKit SDK but
  not run against a live project here; Twilio's current disaster-recovery URL setting on Elastic
  SIP trunks; `VOICE_LLM_FALLBACK_MODEL` with the eval suite before relying on it.
