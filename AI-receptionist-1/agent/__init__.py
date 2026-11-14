"""
LiveKit voice agent — one worker for phone (SIP) and browser calls. Roadmap Phase 1.

    python -m agent.worker dev      # local development (connects to LIVEKIT_URL)
    python -m agent.worker start    # production (systemd: receptionist-agent)

Layout:
    worker.py           entrypoint: one job per call (LiveKit room)
    session_factory.py  STT → LLM → TTS, turn detection, interruptions
    receptionist.py     the Agent: greeting, voice-only tools, speech clean-up, silence handling
    tools.py            core.tools.registry exposed to LiveKit (one implementation for all channels)
    profile.py          business settings for the call (Phase 2: loaded per tenant)
    prompt.py           the voice prompt, static part first so providers can cache it
    telemetry.py        per-turn latency (the Phase 1 acceptance numbers)
    persistence.py      `calls` collection, written off the hot path
"""
