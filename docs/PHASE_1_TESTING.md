# Phase 1 — development testing guide

What's built, what needs your dev accounts, and how to check each Phase 1 acceptance item
(ROADMAP.md §6, Task 1.12.c). Production steps are in [PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md).

## What's in the code

| Roadmap task | Where |
|---|---|
| 1.1 Per-turn latency | `agent/telemetry.py` → `calls.turns`; `scripts/latency_report.py`; dashboard Calls page |
| 1.2 SIP trunk config | `AI-receptionist-1/telephony/` (templates + setup steps) |
| 1.3 Speech stack | Deepgram Nova-3 → `VOICE_LLM_MODEL` (default `gpt-4.1-mini`) → ElevenLabs Flash v2.5, Cartesia/OpenAI TTS fallback |
| 1.4 New worker | `agent/worker.py`, `agent/session_factory.py` (replaces `livekit_agent.py`) |
| 1.5 Voice prompt | `agent/prompt.py` (static first for prompt caching), `agent/speech.py` |
| 1.6 Feels human | cached greeting (`agent/greeting.py`), thinking sound, interruptions, silence handling |
| 1.7 Voice tools | `end_call`, `transfer_to_human` (`agent/receptionist.py`), `take_message` (`core/tools/messages.py`) |
| 1.8 Persistence | `agent/persistence.py` — `calls` collection, flushed every 10 s, final write on hang-up |
| 1.9 Fast tools | one-query availability, WhatsApp sends in the background, timeouts, indexes via `migrations/m0002_indexes.py` |
| 1.10 One tool implementation | `core/tools/registry.py` used by voice (`agent/tools.py`) and WhatsApp (`ai/agent.py`) |
| 1.11 Recording, languages | `agent/recording.py` (off by default), `VOICE_LANGUAGE` |
| 1.12 Tests | `tests/test_agent_*.py`, `tests/test_tools_registry.py`, `tests/evals/` |
| 1.13 Cutover | `LEGACY_TWILIO_VOICE` flag; deletion happens after cutover |

## Before your first test call

1. **`.env`:** a valid `OPENAI_API_KEY` (the current one is rejected with 401), plus
   `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`, and `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET`
   from a **dev** LiveKit Cloud project. Without Deepgram the agent falls back to slower OpenAI STT.
2. **Indexes:** `uv run python -m migrations.m0002_indexes`.
3. **Worker:** on Windows, `docker compose -f docker-compose.dev.yml up --build agent` (or
   `start.bat`); elsewhere, `uv run python -m agent.worker dev`. A local MongoDB is
   `host.docker.internal` from inside the container.
4. **Evals** (a few cents): `docker compose -f docker-compose.dev.yml run --rm -e RUN_EVALS=1 tests pytest tests/evals`.
   All 8 must pass. Re-run them whenever you change the prompt or the model.

## Test without a phone number (browser call)

Send the WhatsApp "call me" keyword to the hotel number and open the link (or generate one:
`uv run python -c "from core.signed_links import build_call_link; print(build_call_link('http://localhost:8002', 'test-room'))"`
with the API running). The link joins a LiveKit room and the agent is dispatched to it.

## Test with a phone number (SIP)

Follow [telephony/README.md](../AI-receptionist-1/telephony/README.md) with a dev trunk and one
test number. On the first call, check the worker log line `SIP participant attributes: [...]`
includes `sip.phoneNumber` and `sip.trunkPhoneNumber`.

## Acceptance checks (Task 1.12.c)

Run the 20-call script (mobile on 4G, a landline, speakerphone in a noisy room, a strong accent;
cover booking, a taken slot, "are you a robot?", asking for a person in and out of
`TRANSFER_HOURS`, an unknown question, a self-correction, someone else's booking, prompt
injection, "thanks, bye", long silence, talking over the greeting, reading a number slowly, a
6-minute call). Then `uv run python -m scripts.latency_report --channel phone`.

| Check | Target | How |
|---|---|---|
| Response p50 / p95, no tool | ≤ 1.0 s / ≤ 1.8 s | latency report |
| Response p50, tool turns | ≤ 1.6 s | latency report |
| Greeting after connect | ≤ 0.7 s | latency report (the first call after a greeting change streams it live, later calls use the cache) |
| Barge-in | agent stops ≤ 300 ms | by ear: say "no wait, Thursday" mid-sentence |
| "mm-hmm" while agent talks | agent keeps talking | by ear |
| Lost utterances | 0 | compare the call transcript (`GET /admin/calls/{id}`) with what you said |
| Fillers on non-tool turns | 0 | transcript |
| Transfer | 5 / 5 | set `TRANSFER_PHONE_NUMBER` to your mobile |
| Eval suite | 100% | step 4 above |
| Calls dropped by a deploy | 0 | restart the worker during a call; the call must finish (production: `TimeoutStopSec=600`) |

If latency misses the target, the report's "where the time goes" section shows which stage to
tune: `end_of_turn_delay` → `VOICE_ENDPOINTING_MIN_DELAY`; `llm_node_ttft` → try another model
with the evals; `tts_node_ttfb` → check ElevenLabs Flash is active (not the OpenAI fallback);
everything high → move the worker closer to LiveKit/Mongo.

## Known limits

- **Windows:** `livekit-agents` crashes on import on this Windows build (native libraries), so
  the worker runs in Docker. LiveKit-dependent tests skip on Windows and run in CI and in the
  Docker `tests` service.
- **livekit-agents is pinned at 1.8.6.** 1.8.7+ needs `openai>=3` (a major upgrade). Move the
  whole LiveKit family together, on purpose, and re-run the evals.
- **Transfers are cold (SIP REFER).** If the staff line doesn't answer, the call is gone. Warm
  transfer (the agent stays on until someone answers) is the next step after cold transfer works.
- **Post-call summaries and owner notifications per call** are not built yet; messages already
  notify the owner (`OWNER_NOTIFY_WHATSAPP`).
