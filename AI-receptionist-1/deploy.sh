#!/usr/bin/env bash
# =============================================================
# deploy.sh — install the checked-out commit and restart services.
# Called by .github/workflows/deploy.yml after CI is green, from the repo root on the server:
#     git checkout <sha> && ./AI-receptionist-1/deploy.sh
# One-time server setup: see DEPLOY_GUIDE.md.
# =============================================================
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BACKEND_DIR"

echo "==> [1/5] Installing exact dependency versions (uv.lock)"
uv sync --frozen --no-dev

echo "==> [2/5] Recording release for error tracking"
echo "GIT_SHA=$(git rev-parse --short HEAD)" > .env.release

echo "==> [3/5] Database indexes (idempotent; never built at startup or inside a call)"
uv run --frozen --no-dev --env-file /etc/receptionist/prod.env python -m migrations.m0002_indexes

echo "==> [4/5] Restarting services"
# receptionist-agent drains: it stops taking new calls and lets active calls finish
# (TimeoutStopSec=600 in its unit), so customers are not cut off mid-call.
sudo systemctl restart receptionist-api
sudo systemctl restart receptionist-agent

echo "==> [5/5] Waiting for the API to report ready"
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8002/readyz >/dev/null; then
    echo "Deploy OK: $(git rev-parse --short HEAD)"
    exit 0
  fi
  sleep 2
done

echo "API did not become ready in 60s. Recent logs:" >&2
sudo journalctl -u receptionist-api -n 50 --no-pager >&2 || true
exit 1
