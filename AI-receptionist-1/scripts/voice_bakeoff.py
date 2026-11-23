"""
Voice bake-off: hear the candidate voices say real dental lines, side by side, before choosing the
platform's AU and UK defaults (CARTESIA_VOICE_ID_AU / _GB; ELEVENLABS_VOICE_ID_AU / _GB if you use it).

    uv run python -m scripts.voice_bakeoff                       # AU and UK, voices picked from Cartesia's library
    uv run python -m scripts.voice_bakeoff --country AU --cartesia-voices ID1,ID2,ID3
    uv run python -m scripts.voice_bakeoff --per-country 6       # hear more of the library

For every candidate × line it writes, under scratch/bakeoff/<time>/:
    <candidate>/<line>.wav        the voice as rendered (24 kHz)
    <candidate>/<line>.phone.wav  what a caller hears: band-limited to 8 kHz and through G.711 µ-law
    results.csv                   time to first audio, characters (≈ cost) and seconds per line
    index.html                    open it to listen; judge by the "phone" column

Text goes through the same clean-up the live agent uses (agent/speech.py), so numbers, prices and
times are spoken the way callers will hear them. Calls each provider's streaming HTTP API directly (no
LiveKit runtime needed, so it runs on Windows too). First-audio times are from THIS machine to the
provider; on the server compare with scripts/latency_report.py after a switch.

Candidates: Cartesia (each model in --cartesia-models × up to --per-country Australian / British voices
from Cartesia's library, or --cartesia-voices; the current CARTESIA_VOICE_ID_AU / _GB always included),
Deepgram Aura in the local accent as the baseline (the fallback voice), and ElevenLabs only when
ELEVENLABS_API_KEY is set. A provider without a key is skipped.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import html
import re
import statistics
import sys
import time
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import httpx

from agent.speech import clean_for_speech, normalize_numbers
from config import settings
from core.voices import AURA_DEFAULT_FOR_COUNTRY, default_voice, is_flux_tts

RATE = 24_000
ROOT = Path(__file__).resolve().parent.parent

# Lines a dental receptionist says every day, per country (fictional practices; ACMA / Ofcom test numbers).
LINES: Dict[str, List[Tuple[str, str]]] = {
    "AU": [
        ("greeting", "Hi, you've reached Harbourline Dental. I'm the practice's AI assistant, and this call is "
                     "transcribed for the clinic. How can I help today?"),
        ("slots", "Sure. I've got Tuesday the 14th at 9:30am, Wednesday at 11:15am, or Thursday at 3pm. "
                  "Which suits you best?"),
        ("empathy", "Oh, I'm sorry, that sounds really uncomfortable. Let's get you in as soon as we can."),
        ("emergency", "If your face is swelling or you're finding it hard to breathe, please hang up and call "
                      "000 straight away."),
        ("price", "A new patient check-up and clean is $195, and you'll get a full written plan before "
                  "anything else is done."),
        ("readback", "Just to confirm, that's 0412 345 678, and your date of birth is the 14th of March, 1985?"),
        ("booked", "Lovely, you're all booked in with Dr Patel on Tuesday at 9:30am. We'll send a reminder "
                   "the day before."),
        ("goodbye", "No worries at all. Thanks for calling Harbourline Dental, have a great day!"),
    ],
    "GB": [
        ("greeting", "Hello, you've reached Wimpole Dental. I'm the practice's AI assistant, and this call is "
                     "transcribed for the practice. How can I help?"),
        ("slots", "Of course. I've got Tuesday the 14th at 9:30am, Wednesday at 11:15am, or Thursday at 3pm. "
                  "Which would suit you?"),
        ("empathy", "Oh, I'm sorry, that sounds really painful. Let's get you seen as soon as we can."),
        ("emergency", "If your face is swelling or you're struggling to breathe, please hang up and call 999, "
                      "or go to A and E."),
        ("price", "A new patient examination is £60, and the dentist will go through every option and cost "
                  "with you first."),
        ("readback", "Just to confirm, that's 07700 900123, and your date of birth is the 14th of March, 1985?"),
        ("booked", "Lovely, you're booked in with Dr Patel on Tuesday at 9:30am. We'll text you a reminder "
                   "the day before."),
        ("goodbye", "Not at all. Thanks for calling Wimpole Dental, take care!"),
    ],
}
ACCENT = {"AU": ("australian", "aussie"), "GB": ("british", "english", "uk")}


@dataclass
class Candidate:
    provider: str          # elevenlabs | cartesia | deepgram
    model: str
    voice_id: str
    label: str
    country: str

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", f"{self.country}-{self.provider}-{self.model}-{self.label}".lower()).strip("-")


@dataclass
class Result:
    candidate: Candidate
    line_id: str
    text: str
    ttfb_s: Optional[float]
    seconds: float
    error: str = ""


# ── Providers (streaming HTTP; first chunk = time to first audio) ───────────

async def _stream(client: httpx.AsyncClient, method: str, url: str, **kw) -> Tuple[bytes, float]:
    start = time.perf_counter()
    first: Optional[float] = None
    buf = bytearray()
    async with client.stream(method, url, **kw) as resp:
        if resp.status_code >= 400:
            body = (await resp.aread()).decode("utf-8", "replace")[:300]
            raise RuntimeError(f"HTTP {resp.status_code}: {body}")
        async for chunk in resp.aiter_bytes():
            if chunk and first is None:
                first = time.perf_counter() - start
            buf.extend(chunk)
    if len(buf) % 2:
        buf = buf[:-1]
    return bytes(buf), first if first is not None else time.perf_counter() - start


async def synth(client: httpx.AsyncClient, c: Candidate, text: str) -> Tuple[bytes, float]:
    """Raw 16-bit mono PCM at 24 kHz, and seconds to the first audio byte."""
    if c.provider == "elevenlabs":
        headers = {"xi-api-key": settings.ELEVENLABS_API_KEY}
        params = {"output_format": f"pcm_{RATE}", "enable_logging": str(not settings.ELEVENLABS_ZDR_ENABLED).lower()}
        if c.model.startswith(("eleven_v3", "eleven_v4")):     # dialogue models (as the LiveKit plugin does)
            body = {"model_id": c.model, "inputs": [{"text": text, "voice_id": c.voice_id}]}
            return await _stream(client, "POST", "https://api.elevenlabs.io/v1/text-to-dialogue/stream",
                                 headers=headers, params=params, json=body)
        body = {"text": text, "model_id": c.model, "apply_text_normalization": "auto"}
        return await _stream(client, "POST", f"https://api.elevenlabs.io/v1/text-to-speech/{c.voice_id}/stream",
                             headers=headers, params=params, json=body)
    if c.provider == "cartesia":
        body = {"model_id": c.model, "transcript": text, "voice": {"mode": "id", "id": c.voice_id},
                "language": "en", "output_format": {"container": "raw", "encoding": "pcm_s16le", "sample_rate": RATE}}
        return await _stream(client, "POST", "https://api.cartesia.ai/tts/bytes", json=body,
                             headers={"X-API-Key": settings.CARTESIA_API_KEY, "Cartesia-Version": "2025-04-16"})
    if c.provider == "deepgram":       # Flux TTS on /v2/speak, Aura on /v1/speak
        params = {"model": c.model, "encoding": "linear16", "sample_rate": str(RATE), "container": "none",
                  "mip_opt_out": str(settings.DEEPGRAM_MIP_OPT_OUT).lower()}
        version = "v2" if is_flux_tts(c.model) else "v1"
        return await _stream(client, "POST", f"https://api.deepgram.com/{version}/speak", params=params, json={"text": text},
                             headers={"Authorization": f"Token {settings.DEEPGRAM_API_KEY}"})
    raise ValueError(c.provider)


# ── What a caller hears: 8 kHz G.711 µ-law ─────────────────────────────────

def telephone(pcm24: bytes) -> bytes:
    """24 kHz PCM → 8 kHz, band-limited (windowed-sinc low-pass at 3.4 kHz) → µ-law and back."""
    import numpy as np

    import warnings

    with warnings.catch_warnings():                      # stdlib audioop is deprecated in 3.12, gone in 3.13
        warnings.simplefilter("ignore", DeprecationWarning)
        import audioop                                   # 3.13: provided by the audioop-lts package

    x = np.frombuffer(pcm24, dtype="<i2").astype(np.float64)
    if x.size == 0:
        return b""
    taps = 129
    n = np.arange(taps) - (taps - 1) / 2
    cutoff = 3400 / RATE
    h = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(taps)
    h /= h.sum()
    y = np.convolve(x, h, mode="same")[::3]
    pcm8 = np.clip(y, -32768, 32767).astype("<i2").tobytes()
    return audioop.ulaw2lin(audioop.lin2ulaw(pcm8, 2), 2)


def write_wav(path: Path, pcm: bytes, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


# ── Candidates ─────────────────────────────────────────────────────────────

async def elevenlabs_candidates(countries: List[str], models: List[str], ids: List[str], per_country: int
                                ) -> List[Candidate]:
    if not settings.ELEVENLABS_API_KEY:
        print("ElevenLabs: no ELEVENLABS_API_KEY, skipped")
        return []
    from core.integrations.elevenlabs import fetch_voices

    library = await fetch_voices()
    by_id = {v["voice_id"]: v for v in library}
    out: List[Candidate] = []
    for cc in countries:
        if ids:
            picks = [by_id.get(i) or {"voice_id": i, "name": i} for i in ids]
        else:
            current = getattr(settings, f"ELEVENLABS_VOICE_ID_{cc}")
            local = [v for v in library if any(a in (v.get("accent") or "").lower() for a in ACCENT[cc])]
            local.sort(key=lambda v: (v["voice_id"] != current, v.get("gender") != "female", v["name"].lower()))
            picks = local[:per_country]
            if not picks:
                print(f"ElevenLabs: no {cc}-accent voice in your library. Add some from the Voice Library "
                      f"(filter by accent), or pass --el-voices.")
        for v in picks:
            for model in models:
                out.append(Candidate("elevenlabs", model, v["voice_id"], v.get("name") or v["voice_id"], cc))
    return out


CARTESIA_ACCENT = {"AU": "Australian", "GB": "British"}


async def cartesia_candidates(countries: List[str], models: List[str], ids: List[str], per_country: int
                              ) -> List[Candidate]:
    if not settings.CARTESIA_API_KEY:
        print("Cartesia: no CARTESIA_API_KEY, skipped")
        return []
    library: List[dict] = []
    if not ids:
        from core.integrations.cartesia import fetch_voices

        try:
            library = await fetch_voices()
        except Exception as e:
            print(f"Cartesia: couldn't list voices ({str(e)[:100]}); pass --cartesia-voices")
    by_id = {v["voice_id"]: v for v in library}
    out: List[Candidate] = []
    for cc in countries:
        current = getattr(settings, f"CARTESIA_VOICE_ID_{cc}")
        if ids:
            picks = [by_id.get(i) or {"voice_id": i, "name": i[:8]} for i in ids]
        else:
            local = [v for v in library if v.get("accent") == CARTESIA_ACCENT[cc]]
            local.sort(key=lambda v: (v["voice_id"] != current, v.get("gender") not in ("female", "feminine"),
                                      v["name"].lower()))
            picks = local[:per_country]
            if current and current not in {v["voice_id"] for v in picks}:
                picks.insert(0, by_id.get(current) or {"voice_id": current, "name": "current"})
            if not picks:
                print(f"Cartesia: no {CARTESIA_ACCENT[cc]} voice found in the library; pass --cartesia-voices")
        for v in picks:
            for model in models:
                out.append(Candidate("cartesia", model, v["voice_id"], v.get("name") or v["voice_id"][:8], cc))
    return out


def aura_candidates(countries: List[str], extra: Sequence[str] = ()) -> List[Candidate]:
    """Deepgram: the platform's fallback voice per country (DEEPGRAM_TTS_MODEL[_AU / _GB]), any
    --deepgram-voices, and the old Aura voice in the local accent as a baseline."""
    if not settings.DEEPGRAM_API_KEY:
        print("Deepgram: no DEEPGRAM_API_KEY, skipped")
        return []
    out: List[Candidate] = []
    for cc in countries:
        fallback, baseline = default_voice("deepgram", cc), AURA_DEFAULT_FOR_COUNTRY[cc]
        for model in dict.fromkeys([fallback, *extra, baseline]):
            label = "fallback" if model == fallback else "aura-baseline" if model == baseline else model
            out.append(Candidate("deepgram", model, model, label, cc))
    return out


# ── Run ────────────────────────────────────────────────────────────────────

async def run(candidates: List[Candidate], out: Path) -> List[Result]:
    results: List[Result] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        for c in candidates:
            print(f"  {c.country} {c.provider:<10} {c.model:<20} {c.label}")
            for line_id, raw in LINES[c.country]:
                text = normalize_numbers(clean_for_speech(raw))
                try:
                    pcm, ttfb = await synth(client, c, text)
                except Exception as e:                      # one bad voice must not stop the bake-off
                    results.append(Result(c, line_id, text, None, 0.0, str(e)[:200]))
                    print(f"      {line_id}: FAILED {str(e)[:120]}")
                    continue
                write_wav(out / c.slug / f"{line_id}.wav", pcm, RATE)
                write_wav(out / c.slug / f"{line_id}.phone.wav", telephone(pcm), 8000)
                results.append(Result(c, line_id, text, ttfb, len(pcm) / 2 / RATE))
    return results


def report(results: List[Result], out: Path) -> None:
    with open(out / "results.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["country", "provider", "model", "voice_id", "voice", "line", "chars", "ttfb_ms", "audio_s", "error"])
        for r in results:
            c = r.candidate
            w.writerow([c.country, c.provider, c.model, c.voice_id, c.label, r.line_id, len(r.text),
                        "" if r.ttfb_s is None else round(r.ttfb_s * 1000), round(r.seconds, 2), r.error])

    cands: List[Candidate] = list({r.candidate.slug: r.candidate for r in results}.values())
    rows = []
    for cc in sorted({c.country for c in cands}):
        mine = [c for c in cands if c.country == cc]
        head = "".join(f"<th>{html.escape(c.provider)}<br><small>{html.escape(c.model)}</small><br>"
                       f"<b>{html.escape(c.label)}</b><br><code>{html.escape(c.voice_id)}</code></th>" for c in mine)
        body = []
        for line_id, raw in LINES[cc]:
            cells = []
            for c in mine:
                r = next((r for r in results if r.candidate.slug == c.slug and r.line_id == line_id), None)
                if r is None or r.error:
                    cells.append(f"<td class=err>{html.escape(r.error if r else 'missing')}</td>")
                    continue
                cells.append(f"<td><div>phone <audio controls preload=none src='{c.slug}/{line_id}.phone.wav'></audio></div>"
                             f"<div>HD <audio controls preload=none src='{c.slug}/{line_id}.wav'></audio></div>"
                             f"<small>{round(r.ttfb_s * 1000)} ms to first audio</small></td>")
            body.append(f"<tr><th class=line>{html.escape(line_id)}<br><small>{html.escape(raw)}</small></th>{''.join(cells)}</tr>")
        rows.append(f"<h2>{cc}</h2><table><tr><th></th>{head}</tr>{''.join(body)}</table>")
    page = ("<!doctype html><meta charset=utf-8><title>Voice bake-off</title><style>"
            "body{font:14px system-ui;margin:24px}table{border-collapse:collapse}td,th{border:1px solid #ddd;"
            "padding:8px;vertical-align:top}th.line{max-width:260px;text-align:left}audio{height:28px;width:200px}"
            ".err{color:#b00}</style><h1>Voice bake-off</h1><p>Judge by the <b>phone</b> player: that is what "
            "callers hear. Then set CARTESIA_VOICE_ID_AU / _GB (and the model) in .env.</p>" + "".join(rows))
    (out / "index.html").write_text(page, encoding="utf-8")

    print("\nTime to first audio (median, this machine → provider):")
    for c in cands:
        t = [r.ttfb_s for r in results if r.candidate.slug == c.slug and r.ttfb_s is not None]
        fails = sum(1 for r in results if r.candidate.slug == c.slug and r.error)
        med = f"{round(statistics.median(t) * 1000)} ms" if t else "—"
        print(f"  {c.country} {c.provider:<10} {c.model:<20} {c.label:<24} {med}" + (f"  ({fails} failed)" if fails else ""))
    print(f"\nListen: {out / 'index.html'}")


async def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):     # Windows consoles default to cp1252 (no → or ×)
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--country", choices=["AU", "GB", "both"], default="both")
    parser.add_argument("--cartesia-models", default=settings.CARTESIA_MODEL, help="e.g. sonic-3,sonic-turbo")
    parser.add_argument("--el-models", default="eleven_v4_turbo,eleven_flash_v2_5", help="only with ELEVENLABS_API_KEY")
    parser.add_argument("--el-voices", default="", help="comma-separated ElevenLabs voice ids (default: by accent)")
    parser.add_argument("--per-country", type=int, default=4, help="library voices per country when picking by accent")
    parser.add_argument("--cartesia-voices", default="", help="comma-separated Cartesia voice ids")
    parser.add_argument("--deepgram-voices", default="", help="extra Deepgram voices, e.g. flux-kit-en,flux-colin-en")
    parser.add_argument("--no-aura", action="store_true", help="skip the Deepgram voices")
    parser.add_argument("--out", default="", help="output folder (default scratch/bakeoff/<time>)")
    args = parser.parse_args()

    countries = ["AU", "GB"] if args.country == "both" else [args.country]
    split = lambda s: [x.strip() for x in s.split(",") if x.strip()]  # noqa: E731
    candidates = (await cartesia_candidates(countries, split(args.cartesia_models), split(args.cartesia_voices),
                                            args.per_country)
                  + ([] if args.no_aura else aura_candidates(countries, split(args.deepgram_voices)))
                  + (await elevenlabs_candidates(countries, split(args.el_models), split(args.el_voices), args.per_country)
                     if settings.ELEVENLABS_API_KEY else []))
    if not candidates:
        sys.exit("No candidates: set CARTESIA_API_KEY (and DEEPGRAM_API_KEY for the Aura baseline) in .env")
    out = Path(args.out) if args.out else ROOT / "scratch" / "bakeoff" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    print(f"Rendering {len(candidates)} voice(s) × {len(LINES[countries[0]])} lines → {out}")
    report(await run(candidates, out), out)


if __name__ == "__main__":
    asyncio.run(main())
