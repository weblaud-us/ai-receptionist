"""
Synthetic call and load test (Roadmap Tasks 3.7 and 3.9).

Places real calls to the voice agent through LiveKit, the way the browser call page does (a room
with the agent dispatched into it), and listens for the agent's voice. It proves the chain
LiveKit → worker → tenant lookup → TTS works, and measures what callers feel.

    # Health check: one call, exit 1 if no greeting is heard within 10 s. Run it every 15 minutes
    # (deploy/monitoring/README.md). On success it pings --heartbeat (Better Stack / Healthchecks.io
    # heartbeat URL); set the heartbeat's grace period to 35 min so two misses in a row page you.
    uv run python -m scripts.synthetic_call --tenant ten_abc123 --heartbeat https://hc-ping.com/…

    # Load test: 20 calls at once, started over 30 s, each held for 60 s.
    uv run python -m scripts.synthetic_call --tenant ten_abc123 --concurrency 20 --ramp 30 --hold 60

    # Scripted caller: after the greeting, play a recording of a question and time the answer.
    # WAV, mono, 16-bit PCM (e.g. `ffmpeg -i question.mp3 -ac 1 -ar 48000 -c:a pcm_s16le question.wav`).
    uv run python -m scripts.synthetic_call --tenant ten_abc123 --concurrency 50 --ramp 60 --say question.wav

    # Through the phone network: dial the business's real number from the outbound SIP trunk
    # (LIVEKIT_SIP_OUTBOUND_TRUNK_ID). Exercises carrier → Twilio trunk → LiveKit SIP → dispatch rule
    # → worker. The greeting is timed from the moment the call is answered.
    uv run python -m scripts.synthetic_call --dial +15550001111 --heartbeat https://hc-ping.com/…

What it measures, per call: seconds to the first agent audio (greeting), and with --say the
seconds between the end of the caller's question and the start of the agent's answer.
Server-side numbers for the same calls: `python -m scripts.latency_report` and /metrics.

Limits, stated plainly:
- Without --dial it is a browser-type call: it does NOT exercise the phone number, the SIP trunk
  or the carrier. --dial does, and costs carrier minutes on both legs (outbound + inbound).
  The outbound number must be allowed to call the inbound one (Twilio geo permissions).
- Calls are real: they use LLM / STT / TTS credit and count against the tenant's concurrent-call
  limit (raise `limits.max_concurrent_calls` on the test tenant before a load test).
- Run it on Linux or in the dev container: LiveKit's native library crashes on some Windows builds.
  ⚠ Written against livekit 1.1.20 / livekit-api 1.2.2 and not yet run against a live project —
  do one single-call run and compare with the call record before trusting a load test.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
import uuid
import wave
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, List, Optional

from config import settings

SILENCE_RMS = 150.0          # int16 RMS below this is silence (comfort noise is ~0–50)
END_OF_SPEECH_S = 1.0        # this much agent silence = the greeting is over
FRAME_MS = 20


@dataclass
class CallResult:
    room: str
    ok: bool = False
    greeting_s: Optional[float] = None
    response_s: Optional[float] = None
    error: str = ""


def _token(room: str, tenant_id: Optional[str], *, dispatch: bool = True) -> str:
    """dispatch=True: the agent joins this room (browser-type call). False: just us (--dial)."""
    from livekit import api

    token = (api.AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
             .with_identity(f"synthetic_{uuid.uuid4().hex[:8]}")
             .with_ttl(timedelta(minutes=30))
             .with_grants(api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True)))
    if dispatch:
        token = token.with_room_config(api.RoomConfiguration(
            agents=[api.RoomAgentDispatch(agent_name=settings.LIVEKIT_AGENT_NAME)]))
    if tenant_id:
        token = token.with_attributes({"tenant_id": tenant_id})
    return token.to_jwt()


def _rms(frame: Any) -> float:
    import numpy as np

    samples = np.frombuffer(frame.data, dtype=np.int16)
    return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2))) if samples.size else 0.0


class _Listener:
    """Tracks when the agent is speaking, from the audio it publishes."""

    def __init__(self) -> None:
        self.speech_started = asyncio.Event()
        self.last_voice_at: Optional[float] = None

    async def run(self, track: Any) -> None:
        from livekit import rtc

        async for event in rtc.AudioStream(track):
            if _rms(event.frame) >= SILENCE_RMS:
                self.last_voice_at = time.monotonic()
                self.speech_started.set()

    async def wait_for_speech(self, timeout: float) -> bool:
        self.speech_started.clear()
        try:
            await asyncio.wait_for(self.speech_started.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def wait_for_silence(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.last_voice_at is not None and time.monotonic() - self.last_voice_at >= END_OF_SPEECH_S:
                return
            await asyncio.sleep(0.05)


async def _play_wav(room: Any, path: str) -> None:
    from livekit import rtc

    with wave.open(path, "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ValueError("--say needs a mono 16-bit PCM WAV")
        rate = wav.getframerate()
        source = rtc.AudioSource(rate, 1)
        track = rtc.LocalAudioTrack.create_audio_track("synthetic-caller", source)
        await room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        samples_per_frame = rate * FRAME_MS // 1000
        while True:
            data = wav.readframes(samples_per_frame)
            if not data:
                break
            if len(data) < samples_per_frame * 2:
                data += b"\x00" * (samples_per_frame * 2 - len(data))
            await source.capture_frame(rtc.AudioFrame(data, rate, 1, samples_per_frame))
        await source.wait_for_playout()


async def _dial(room_name: str, number: str, timeout: float) -> Any:
    """Ring `number` from the outbound trunk into our room; returns once it is answered."""
    from livekit import api

    lk = api.LiveKitAPI(settings.LIVEKIT_URL, settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
    try:
        await lk.sip.create_sip_participant(api.CreateSIPParticipantRequest(
            sip_trunk_id=settings.LIVEKIT_SIP_OUTBOUND_TRUNK_ID, sip_call_to=number, room_name=room_name,
            participant_identity=f"pstn_{uuid.uuid4().hex[:6]}", participant_name="synthetic call",
            wait_until_answered=True), timeout=timeout + 30)
    finally:
        await lk.aclose()


async def _hang_up(room_name: str) -> None:
    from livekit import api

    lk = api.LiveKitAPI(settings.LIVEKIT_URL, settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
    try:
        await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))   # drops the phone leg too
    except Exception:
        pass
    finally:
        await lk.aclose()


async def one_call(tenant_id: Optional[str], *, timeout: float, hold: float, say: Optional[str],
                   dial: Optional[str] = None) -> CallResult:
    from livekit import rtc

    result = CallResult(room=f"synthetic-{uuid.uuid4().hex[:10]}")
    room = rtc.Room()
    listener = _Listener()
    tasks: List[asyncio.Task[None]] = []

    @room.on("track_subscribed")
    def _on_track(track: Any, publication: Any, participant: Any) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            tasks.append(asyncio.create_task(listener.run(track)))

    try:
        started = time.monotonic()
        await room.connect(settings.LIVEKIT_URL, _token(result.room, tenant_id, dispatch=dial is None))
        if dial:
            await _dial(result.room, dial, timeout)
            started = time.monotonic()          # time the greeting from "answered", not from ringing
        if not await listener.wait_for_speech(timeout):
            result.error = f"no agent audio within {timeout:.0f}s"
            return result
        result.greeting_s = round(time.monotonic() - started, 2)
        if say:
            await listener.wait_for_silence(20)
            await _play_wav(room, say)
            asked = time.monotonic()
            if not await listener.wait_for_speech(timeout):
                result.error = f"no answer within {timeout:.0f}s of the question"
                return result
            result.response_s = round(time.monotonic() - asked, 2)
        if hold > 0:
            await asyncio.sleep(hold)
        result.ok = True
        return result
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        return result
    finally:
        for task in tasks:
            task.cancel()
        try:
            await room.disconnect()      # the agent's room closes when the caller leaves
        except Exception:
            pass
        if dial:
            await _hang_up(result.room)


def _pct(values: List[float], pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(pct / 100 * len(ordered) + 0.5) - 1))]


def report(results: List[CallResult]) -> int:
    ok = [r for r in results if r.ok]
    print(f"\ncalls: {len(results)}   ok: {len(ok)}   failed: {len(results) - len(ok)}")
    for label, values in (("greeting (time to first audio)", [r.greeting_s for r in results if r.greeting_s]),
                          ("response to the scripted question", [r.response_s for r in results if r.response_s])):
        if values:
            print(f"{label}: p50 {statistics.median(values):.2f}s   p95 {_pct(values, 95):.2f}s   "
                  f"max {max(values):.2f}s   (n={len(values)})")
    for r in results:
        if not r.ok:
            print(f"  FAILED {r.room}: {r.error}")
    return 0 if len(ok) == len(results) else 1


async def run(args: argparse.Namespace) -> int:
    needed = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET") + (
        ("LIVEKIT_SIP_OUTBOUND_TRUNK_ID",) if args.dial else ())
    missing = [v for v in needed if not getattr(settings, v)]
    if missing:
        print(f"Missing settings: {', '.join(missing)}", file=sys.stderr)
        return 2

    async def delayed(i: int) -> CallResult:
        if args.concurrency > 1 and args.ramp > 0:
            await asyncio.sleep(args.ramp * i / args.concurrency)
        r = await one_call(args.tenant, timeout=args.timeout, hold=args.hold, say=args.say, dial=args.dial)
        print(f"[{i + 1}/{args.concurrency}] {'ok' if r.ok else 'FAILED'} greeting={r.greeting_s}s "
              f"response={r.response_s}s {r.error}".rstrip(), flush=True)
        return r

    code = report(list(await asyncio.gather(*(delayed(i) for i in range(args.concurrency)))))
    if code == 0 and args.heartbeat:
        await _ping(args.heartbeat)
    return code


async def _ping(url: str) -> None:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)) as client:
            (await client.get(url)).raise_for_status()
    except Exception as exc:
        print(f"heartbeat ping failed: {type(exc).__name__}", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser(description="Synthetic call / load test against the voice agent")
    p.add_argument("--tenant", help="tenant id to call (omit for the legacy .env hotel)")
    p.add_argument("--concurrency", type=int, default=1, help="calls at the same time (default 1)")
    p.add_argument("--ramp", type=float, default=0, help="seconds over which the calls are started")
    p.add_argument("--hold", type=float, default=0, help="seconds to stay on the line after the checks")
    p.add_argument("--timeout", type=float, default=10, help="seconds to wait for agent audio (default 10)")
    p.add_argument("--say", help="mono 16-bit WAV played after the greeting; the answer is timed")
    p.add_argument("--dial", help="E.164 number to call through the outbound SIP trunk (real phone path)")
    p.add_argument("--heartbeat", help="URL to GET when every call succeeded (uptime-tool heartbeat)")
    raise SystemExit(asyncio.run(run(p.parse_args())))


if __name__ == "__main__":
    main()
