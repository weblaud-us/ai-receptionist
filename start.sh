#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

if [[ "${SEED_ROOMS_ON_START:-false}" == "true" ]]; then
  python -m utils.seed_rooms
fi

exec python -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-8002}"
