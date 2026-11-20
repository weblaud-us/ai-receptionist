#!/usr/bin/env bash
# =============================================================
# Roll one release onto this host without dropping a call (Roadmap Task 3.6).
#
#   deploy/deploy.sh <tag> <staging|production>
#
# Called by .github/workflows/deploy.yml over SSH. Safe to run by hand; the same command with the
# previous tag is the rollback. Needs: Docker with the compose plugin, `docker login ghcr.io`
# done once, /etc/receptionist/<environment>.env (app settings) and
# /etc/receptionist/<environment>.deploy (IMAGE_PREFIX, API_DOMAIN, APP_DOMAIN, ADMIN_DOMAIN). See deploy/README.md.
#
# Order matters:
#   1. indexes          idempotent migration, never at boot or inside a call
#   2. jobs             cron jobs are unique per tick, so old and new can overlap
#   3. api-a, api-b     one at a time; Caddy routes around the one restarting
#   4. worker-a, -b     one at a time; each drains its live calls (up to LIVEKIT_DRAIN_TIMEOUT)
#                       while the other answers new calls
#   5. sales-worker     only when OUTBOUND_DIALER_ENABLED=true in the env file (Phase 4)
# =============================================================
set -euo pipefail

TAG="${1:?usage: deploy.sh <tag> <staging|production>}"
DEPLOY_ENV="${2:?usage: deploy.sh <tag> <staging|production>}"
cd "$(dirname "$0")"

ENV_FILE="/etc/receptionist/${DEPLOY_ENV}.env"
DEPLOY_FILE="/etc/receptionist/${DEPLOY_ENV}.deploy"
[ -f "$ENV_FILE" ] || { echo "Missing $ENV_FILE" >&2; exit 1; }
[ -f "$DEPLOY_FILE" ] || { echo "Missing $DEPLOY_FILE (IMAGE_PREFIX, API_DOMAIN, APP_DOMAIN, ADMIN_DOMAIN)" >&2; exit 1; }
set -a
# shellcheck disable=SC1090
. "$DEPLOY_FILE"
set +a
export TAG DEPLOY_ENV ENV_FILE

# Regional stacks (deploy/regions/): the env file's DEPLOYMENT_REGION is where the data lives. The
# .deploy file names the region the host is in; a mismatch would put AU or UK health data in the wrong
# region, so refuse it. Environments named *-au / *-uk must say which region they are.
env_region="$( { grep -E '^[[:space:]]*DEPLOYMENT_REGION=' "$ENV_FILE" || true; } | tail -n1 | cut -d= -f2- | tr -d "\"' ")"
if [ -n "${DEPLOYMENT_REGION:-}" ] && [ "$env_region" != "$DEPLOYMENT_REGION" ]; then
  echo "DEPLOYMENT_REGION is '${env_region}' in $ENV_FILE but '${DEPLOYMENT_REGION}' in $DEPLOY_FILE" >&2
  exit 1
fi
case "$DEPLOY_ENV" in
  *-au) [ "$env_region" = "ap-southeast-2" ] || { echo "$DEPLOY_ENV must run with DEPLOYMENT_REGION=ap-southeast-2" >&2; exit 1; } ;;
  *-uk) [ "$env_region" = "eu-west-2" ] || { echo "$DEPLOY_ENV must run with DEPLOYMENT_REGION=eu-west-2" >&2; exit 1; } ;;
esac
DEPLOYMENT_REGION="${env_region:-global}"
export DEPLOYMENT_REGION
echo "==> Region: ${DEPLOYMENT_REGION}"

dc() { docker compose -p "receptionist-${DEPLOY_ENV}" -f docker-compose.prod.yml "$@"; }

# The legacy Twilio Media Streams path keeps each call's state in the API process that answered
# /voice/incoming, so it needs exactly one API replica. It is off unless the env file says true.
legacy="$(grep -E '^[[:space:]]*LEGACY_TWILIO_VOICE=' "$ENV_FILE" | tail -n1 | cut -d= -f2- | tr -d "\"' " | tr '[:upper:]' '[:lower:]')"
if [ "$legacy" = "true" ] || [ "$legacy" = "1" ]; then
  API_REPLICAS="api-a"
  echo "!! LEGACY_TWILIO_VOICE is on: running ONE API replica (a restart briefly interrupts webhooks)."
  echo "!! Finish the SIP cutover and set LEGACY_TWILIO_VOICE=false to get two."
else
  API_REPLICAS="api-a api-b"
fi

# Voice worker containers: 2 (default) to 4, from WORKERS in the .deploy file.
WORKERS="${WORKERS:-2}"
case "$WORKERS" in
  2) WORKER_SERVICES="worker-a worker-b" ;;
  3) WORKER_SERVICES="worker-a worker-b worker-c"; export COMPOSE_PROFILES="workers-3" ;;
  4) WORKER_SERVICES="worker-a worker-b worker-c worker-d"; export COMPOSE_PROFILES="workers-4" ;;
  *) echo "WORKERS must be 2, 3 or 4 (got $WORKERS)" >&2; exit 1 ;;
esac

# The outbound sales dialer has its own worker; it only exists when the dialer is switched on.
dialer="$(grep -E '^[[:space:]]*OUTBOUND_DIALER_ENABLED=' "$ENV_FILE" | tail -n1 | cut -d= -f2- | tr -d "\"' " | tr '[:upper:]' '[:lower:]')"
if [ "$dialer" = "true" ] || [ "$dialer" = "1" ]; then
  SALES_WORKER=1
  export COMPOSE_PROFILES="${COMPOSE_PROFILES:+$COMPOSE_PROFILES,}dialer"
else
  SALES_WORKER=0
fi

echo "==> [1/6] Pulling images for ${TAG}"
dc pull --quiet

echo "==> [2/6] Database migrations (idempotent)"
dc up -d --wait redis
dc run --rm --no-deps jobs python -m migrations.m0002_indexes
dc run --rm --no-deps jobs python -m migrations.m0004_encrypt_guest_ids --apply
dc run --rm --no-deps jobs python -m migrations.m0005_encrypt_clinical_text --apply

echo "==> [3/6] Jobs worker"
dc up -d --wait --no-deps jobs

echo "==> [4/6] API replicas, one at a time"
for svc in $API_REPLICAS; do
  dc up -d --wait --no-deps "$svc"
done
if [ "$API_REPLICAS" = "api-a" ]; then
  dc rm -sf api-b >/dev/null 2>&1 || true      # Caddy's health check skips the missing upstream
fi

echo "==> [5/6] Voice workers, one at a time (each waits for its live calls to finish)"
for svc in $WORKER_SERVICES; do
  dc up -d --wait --no-deps "$svc"
done
# Scaled down? Stop the extra workers (each drains its calls first).
for svc in worker-c worker-d; do
  case " $WORKER_SERVICES " in *" $svc "*) ;; *) COMPOSE_PROFILES="workers-4" dc rm -sf "$svc" >/dev/null 2>&1 || true ;; esac
done

if [ "$SALES_WORKER" = "1" ]; then
  echo "==> Sales worker (outbound dialer)"
  dc up -d --wait --no-deps sales-worker
else
  COMPOSE_PROFILES="dialer" dc rm -sf sales-worker >/dev/null 2>&1 || true
fi

echo "==> [6/6] Dashboard, platform admin app and proxy"
dc up -d --wait --no-deps dashboard platform-dashboard
dc up -d --no-deps caddy

for _ in $(seq 1 30); do
  if curl -fsS "https://${API_DOMAIN}/readyz" >/dev/null; then
    echo "Deploy OK: ${TAG} on ${DEPLOY_ENV}"
    exit 0
  fi
  sleep 2
done
echo "https://${API_DOMAIN}/readyz did not become ready. Recent API logs:" >&2
dc logs --tail 50 api-a api-b >&2 || true
exit 1
