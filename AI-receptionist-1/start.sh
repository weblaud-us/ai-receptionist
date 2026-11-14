#!/usr/bin/env bash
# Local development: API with auto-reload. Needs uv (https://docs.astral.sh/uv/).
set -euo pipefail
cd "$(dirname "$0")"

uv sync --frozen

# Seed a dev database by hand when needed: uv run python -m utils.seed_rooms (refuses in production)

exec uv run --frozen uvicorn main:app --host 127.0.0.1 --port "${PORT:-8002}" --reload
