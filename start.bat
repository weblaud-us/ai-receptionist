@echo off
setlocal

cd /d "%~dp0"

echo Starting API server...
start "Hotel AI API" cmd /k ".venv\Scripts\python -m uvicorn main:app --host 0.0.0.0 --port 8002 --reload"

echo Starting LiveKit agent...
start "LiveKit Agent" cmd /k ".venv\Scripts\python livekit_agent.py start"

echo Starting ngrok server
start "ngrok http port 8002" cmd /k "ngrok-v3-stable\ngrok http 8002"

echo Both processes started.
