"""
Real audio for the landing page's sample calls ("Hear it handle real dental calls"), in the voice the
agent really uses, so visitors who don't want to use a microphone still hear it.

    uv run python -m scripts.render_sample_calls                    # every scenario, AU and UK
    uv run python -m scripts.render_sample_calls --only emergency --patient-au <voice id>

Reads the scripts in landing-page/src/data/scenarios.ts. Agent lines use the platform's voice for that
country (Cartesia: CARTESIA_VOICE_ID_AU / _GB with CARTESIA_MODEL); patient lines use a second,
contrasting voice (--patient-au / --patient-gb, else another library voice with the same accent).
System-only scenarios (the text-back one) have nothing to say and are skipped.

Writes, per scenario and region:
    landing-page/public/audio/<id>-<region>.mp3          (or .wav when ffmpeg isn't installed)
    landing-page/src/data/recordings/<id>-<region>.json  {"duration": s, "at": [start of each line], "audio": file}
The landing page picks them up at build time (src/data/recordings.ts): the player plays the file and the
transcript and agent trace follow its real timing. Rebuild the landing page afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import httpx

from agent.speech import clean_for_speech, normalize_numbers
from config import settings
from core.voices import default_voice
from scripts.voice_bakeoff import CARTESIA_ACCENT, RATE, Candidate, synth, write_wav

REPO = Path(__file__).resolve().parents[2]
SCENARIOS_TS = REPO / "landing-page" / "src" / "data" / "scenarios.ts"
OUT = REPO / "landing-page" / "public" / "audio"
TIMINGS = REPO / "landing-page" / "src" / "data" / "recordings"
REGION_COUNTRY = {"au": "AU", "uk": "GB"}
GAP_S = 0.35            # breath between turns
TAIL_S = 0.8

_LINE = re.compile(r"""\{\s*at:\s*([\d.]+),\s*who:\s*'(\w+)',\s*text:\s*(['"`])((?:\\.|(?!\3).)*)\3""", re.S)


@dataclass
class Line:
    at: float
    who: str
    text: str


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), s)


def parse_scenarios(source: str) -> Dict[str, Dict[str, List[Line]]]:
    """{scenario id: {"au": [lines], "uk": [lines]}} from scenarios.ts (lines only, not the trace)."""
    out: Dict[str, Dict[str, List[Line]]] = {}
    starts = [(m.start(), m.group(1)) for m in re.finditer(r"\n    id: '([\w-]+)'", source)]
    for i, (pos, sid) in enumerate(starts):
        chunk = source[pos: starts[i + 1][0] if i + 1 < len(starts) else len(source)]
        au, uk = chunk.find("\n      au: {"), chunk.find("\n      uk: {")
        if au < 0 or uk < 0:
            continue
        regions = {"au": chunk[au:uk], "uk": chunk[uk:]}
        out[sid] = {r: [Line(float(m.group(1)), m.group(2), _unescape(m.group(4))) for m in _LINE.finditer(body)]
                    for r, body in regions.items()}
    return out


def place(lines: List[Line], seconds: List[float]) -> tuple[List[float], float]:
    """Start times: the script's own `at`, pushed later only when the previous line is still speaking."""
    starts: List[float] = []
    end = 0.0
    for line, length in zip(lines, seconds):
        start = max(line.at, end + GAP_S if starts else line.at)
        starts.append(round(start, 2))
        end = start + length
    return starts, round(end + TAIL_S, 1)


def mix(pcm: List[bytes], starts: List[float], duration: float) -> bytes:
    total = int(duration * RATE) * 2
    buf = bytearray(total)
    for chunk, start in zip(pcm, starts):
        at = int(start * RATE) * 2
        buf[at: at + len(chunk)] = chunk[: max(0, total - at)]
    return bytes(buf)


def encode(wav: Path) -> Path:
    """MP3 when ffmpeg is available (smaller for the web), else keep the WAV."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return wav
    mp3 = wav.with_suffix(".mp3")
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(wav), "-codec:a", "libmp3lame", "-b:a", "96k", str(mp3)],
                   check=True)
    wav.unlink()
    return mp3


PROVIDER = "cartesia"      # the platform's voice


async def patient_voice(cc: str, agent_voice: str, given: str) -> str:
    if given:
        return given
    from core.integrations.cartesia import fetch_voices

    for v in await fetch_voices():
        if v["voice_id"] != agent_voice and v.get("accent") == CARTESIA_ACCENT[cc]:
            return v["voice_id"]
    raise SystemExit(f"No second {CARTESIA_ACCENT[cc]} voice in Cartesia's library: "
                     f"pass --patient-{'au' if cc == 'AU' else 'gb'}")


async def render(sid: str, region: str, lines: List[Line], patient: str, client: httpx.AsyncClient) -> Optional[Path]:
    spoken = [ln for ln in lines if ln.who in ("assistant", "patient")]
    if not spoken or len(spoken) != len(lines):
        print(f"  {sid}-{region}: skipped (system lines only)")
        return None
    cc = REGION_COUNTRY[region]
    agent = Candidate(PROVIDER, settings.CARTESIA_MODEL, default_voice(PROVIDER, cc), "agent", cc)
    caller = Candidate(PROVIDER, settings.CARTESIA_MODEL, patient, "patient", cc)
    pcm: List[bytes] = []
    for ln in lines:
        text = normalize_numbers(clean_for_speech(ln.text))
        audio, _ = await synth(client, agent if ln.who == "assistant" else caller, text)
        pcm.append(audio)
    starts, duration = place(lines, [len(p) / 2 / RATE for p in pcm])
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / f"{sid}-{region}.wav"
        write_wav(wav, mix(pcm, starts, duration), RATE)
        final = OUT / f"{sid}-{region}{'.mp3' if shutil.which('ffmpeg') else '.wav'}"
        shutil.move(str(encode(wav)), final)
    for old in OUT.glob(f"{sid}-{region}.*"):            # a previous run in the other format
        if old.suffix in (".mp3", ".wav") and old != final:
            old.unlink()
    TIMINGS.mkdir(parents=True, exist_ok=True)
    (TIMINGS / f"{sid}-{region}.json").write_text(
        json.dumps({"duration": duration, "at": starts, "audio": final.name}, indent=2) + "\n", encoding="utf-8")
    drift = max(s - ln.at for s, ln in zip(starts, lines))
    print(f"  {sid}-{region}: {duration:.1f}s → {final.relative_to(REPO)}" + (f" (lines moved up to {drift:.1f}s later)" if drift > 0.3 else ""))
    return final


async def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):     # Windows consoles default to cp1252 (no → or ×)
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", default="", help="comma-separated scenario ids")
    parser.add_argument("--patient-au", default="", help="Cartesia voice id for AU callers")
    parser.add_argument("--patient-gb", default="", help="Cartesia voice id for UK callers")
    args = parser.parse_args()
    if not settings.CARTESIA_API_KEY:
        sys.exit("Set CARTESIA_API_KEY (and CARTESIA_VOICE_ID_AU / _GB) in .env first")
    for cc in ("AU", "GB"):
        if not default_voice(PROVIDER, cc):
            sys.exit(f"Set CARTESIA_VOICE_ID_{cc} (pick it with scripts/voice_bakeoff.py) first")
    scenarios = parse_scenarios(SCENARIOS_TS.read_text(encoding="utf-8"))
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    if not shutil.which("ffmpeg"):
        print("ffmpeg not found: writing WAV files (larger). Install ffmpeg for MP3.")
    patients = {"AU": await patient_voice("AU", default_voice(PROVIDER, "AU"), args.patient_au),
                "GB": await patient_voice("GB", default_voice(PROVIDER, "GB"), args.patient_gb)}
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0)) as client:
        for sid, regions in scenarios.items():
            if only and sid not in only:
                continue
            for region, lines in regions.items():
                await render(sid, region, lines, patients[REGION_COUNTRY[region]], client)
    print("\nRebuild the landing page: the sample calls now play these files, in step with their transcripts.")


if __name__ == "__main__":
    asyncio.run(main())
