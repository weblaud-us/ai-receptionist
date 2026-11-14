@echo off
setlocal

cd /d "%~dp0"

rem Local development. Needs uv (https://docs.astral.sh/uv/).
uv sync --frozen || exit /b 1

echo Starting API server...
start "Receptionist API" cmd /k "uv run --frozen uvicorn main:app --host 127.0.0.1 --port 8002 --reload"

echo Starting LiveKit agent...
start "LiveKit Agent" cmd /k "uv run --frozen python livekit_agent.py dev"

echo Both processes started.
