#!/usr/bin/env bash
# Dev server: build this clone and run it (deploy/docker-compose.dev-server.yml).
#
#   ./deploy/dev-server.sh                 git pull, build, start everything, show status
#   ./deploy/dev-server.sh up --profile dialer   the same, plus the outbound sales dialer
#   ./deploy/dev-server.sh logs [service]  follow logs (all services, or one: api, worker, jobs, caddy…)
#   ./deploy/dev-server.sh ps              status
#   ./deploy/dev-server.sh restart [svc]   restart without rebuilding
#   ./deploy/dev-server.sh down            stop everything (data volumes are kept)
#   ./deploy/dev-server.sh exec api python -m scripts.seed_web_demo     any other compose command
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE=deploy/dev-server.env
BACKEND_ENV=AI-receptionist-1/.env

[ -f "$ENV_FILE" ] || { echo "Missing $ENV_FILE: cp deploy/dev-server.env.example $ENV_FILE and fill it in." >&2; exit 1; }
[ -f "$BACKEND_ENV" ] || { echo "Missing $BACKEND_ENV: cp AI-receptionist-1/.env.example $BACKEND_ENV and fill it in." >&2; exit 1; }
pw="$(grep -E '^[[:space:]]*REDIS_PASSWORD=' "$ENV_FILE" | tail -n1 | cut -d= -f2- | tr -d "\"' ")"
[ -n "$pw" ] || { echo "Set REDIS_PASSWORD in $ENV_FILE (openssl rand -hex 24)." >&2; exit 1; }

compose() { docker compose --env-file "$ENV_FILE" -f deploy/docker-compose.dev-server.yml "$@"; }

cmd="${1:-up}"
[ $# -gt 0 ] && shift
case "$cmd" in
  up)
    if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      echo "==> git pull"
      git pull --ff-only
    fi
    echo "==> build and start"
    profiles=()
    if [ "${1:-}" = "--profile" ] && [ -n "${2:-}" ]; then profiles=(--profile "$2"); fi
    compose "${profiles[@]}" up -d --build --remove-orphans
    docker image prune -f >/dev/null || true
    compose ps
    ;;
  logs) compose logs -f --tail=200 "$@" ;;
  *)    compose "$cmd" "$@" ;;
esac
