@echo off
setlocal

cd /d "%~dp0"

rem Local development. Needs uv (https://docs.astral.sh/uv/) and Docker Desktop.
uv sync --frozen || exit /b 1

echo Starting API server...
start "Receptionist API" cmd /k "uv run --frozen uvicorn main:app --host 127.0.0.1 --port 8002 --reload"

rem The voice worker runs in Linux: livekit-agents' native libraries crash on some Windows builds.
echo Starting voice agent (Docker)...
start "Voice Agent" cmd /k "docker compose -f docker-compose.dev.yml up --build agent"

echo Both processes started.
