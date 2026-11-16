"""
Check that the settings needed for test calls are filled in and that each key actually works.

    uv run python -m scripts.check_env

Read-only: it lists or pings, never creates anything, and never prints a key or password (only
OK / WARN / FAIL and a hint). Run it after editing .env, before starting the worker.
See docs/TELEPHONY_TESTING.md → "Environment variables".
"""

from __future__ import annotations

import asyncio
import re
from typing import Awaitable, Callable

import httpx

from config import settings

_PLACEHOLDER = re.compile(r"REPLACE|your-|example\.com|xxxx|change-in-production|<.*>", re.I)
Result = tuple[str, str]   # (OK|WARN|FAIL, message)


def _missing(name: str) -> Result | None:
    v = str(getattr(settings, name, "") or "")
    if not v:
        return "FAIL", "empty. Add it to .env"
    if _PLACEHOLDER.search(v):
        return "FAIL", "still the placeholder from .env.example"
    return None


def _http_result(status: int, what: str) -> Result:
    if status == 200:
        return "OK", f"{what} accepted the key"
    if status in (401, 403):
        return "FAIL", f"{what} rejected it (HTTP {status}): wrong, revoked or expired key"
    if status == 429:
        return "WARN", f"{what} says rate-limited or out of credit (HTTP 429): check billing"
    return "FAIL", f"{what} answered HTTP {status}"


async def check_openai(c: httpx.AsyncClient) -> Result:
    if (m := _missing("OPENAI_API_KEY")):
        return m
    r = await c.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"})
    return _http_result(r.status_code, "OpenAI")


async def check_deepgram(c: httpx.AsyncClient) -> Result:
    if not settings.DEEPGRAM_API_KEY:
        return "WARN", "empty: no Deepgram speech-to-text or Aura voice (falls back to slower OpenAI STT)"
    if (m := _missing("DEEPGRAM_API_KEY")):
        return m
    r = await c.get("https://api.deepgram.com/v1/projects", headers={"Authorization": f"Token {settings.DEEPGRAM_API_KEY}"})
    if r.status_code != 200 or "deepgram" not in settings.tts_order(settings.VOICE_TTS_PROVIDER):
        return _http_result(r.status_code, "Deepgram (speech-to-text and Aura voice)")
    # Speak two characters with the configured Aura voice: proves the model name and the key's TTS access.
    s = await c.post("https://api.deepgram.com/v1/speak", params={"model": settings.DEEPGRAM_TTS_MODEL, "encoding": "linear16", "sample_rate": 24000},
                     headers={"Authorization": f"Token {settings.DEEPGRAM_API_KEY}"}, json={"text": "Hi"})
    if s.status_code == 200 and len(s.content) > 1000:
        return "OK", f"key accepted; Aura voice '{settings.DEEPGRAM_TTS_MODEL}' spoke"
    if s.status_code in (400, 404):
        return "FAIL", f"key works but DEEPGRAM_TTS_MODEL '{settings.DEEPGRAM_TTS_MODEL}' was refused (HTTP {s.status_code}): pick one from Deepgram's model list"
    return _http_result(s.status_code, "Deepgram voice")


async def check_elevenlabs(c: httpx.AsyncClient) -> Result:
    if not settings.ELEVENLABS_API_KEY:
        return "WARN", "empty: the agent falls back to Cartesia or OpenAI voices if configured"
    if (m := _missing("ELEVENLABS_API_KEY")):
        return m
    r = await c.get(f"https://api.elevenlabs.io/v1/voices/{settings.ELEVENLABS_VOICE_ID}",
                    headers={"xi-api-key": settings.ELEVENLABS_API_KEY})
    if r.status_code == 404:
        return "FAIL", "key works but ELEVENLABS_VOICE_ID doesn't exist in your account"
    if r.status_code == 401 and "permission" in r.text.lower():
        return "WARN", "key is valid but restricted. Enable Text to Speech (and Voices: read) on the key"
    return _http_result(r.status_code, "ElevenLabs")


async def check_cartesia(c: httpx.AsyncClient) -> Result:
    if not settings.CARTESIA_API_KEY:
        return "WARN", "empty: set it to use the free Cartesia voice (see docs/TELEPHONY_TESTING.md, 0.5)"
    if (m := _missing("CARTESIA_API_KEY")):
        return m
    r = await c.get("https://api.cartesia.ai/voices?limit=1",
                    headers={"X-API-Key": settings.CARTESIA_API_KEY, "Cartesia-Version": "2024-11-13"})
    return _http_result(r.status_code, "Cartesia")


def check_voice_choice() -> Result:
    """Which voices will speak, in order, given the keys and VOICE_TTS_PROVIDER / VOICE_TTS_FALLBACKS."""
    wanted = settings.tts_order(settings.VOICE_TTS_PROVIDER)
    usable = [n for n in wanted if settings.tts_available(n)]
    if not usable:
        return "FAIL", "no voice configured: set DEEPGRAM_API_KEY, KOKORO_BASE_URL, CARTESIA_API_KEY, ELEVENLABS_API_KEY or OPENAI_API_KEY"
    order = " → ".join(usable)
    if usable[0] != settings.VOICE_TTS_PROVIDER:
        return "WARN", (f"VOICE_TTS_PROVIDER={settings.VOICE_TTS_PROVIDER} is not usable (no key / URL), "
                        f"so the order is: {order}")
    return "OK", f"order: {order}" + ("" if len(usable) > 1 else " (no fallback configured)")


async def check_kokoro(c: httpx.AsyncClient) -> Result:
    if not settings.KOKORO_BASE_URL:
        return "WARN", "off (KOKORO_BASE_URL empty). Optional local fallback voice, see docs/TELEPHONY_TESTING.md 0.5"
    base = settings.KOKORO_BASE_URL.rstrip("/")
    root = base[:-3] if base.endswith("/v1") else base
    # Inside Docker the server is "kokoro" / host.docker.internal; from this PC it is localhost.
    from_this_pc = re.sub(r"//(kokoro|host\.docker\.internal)", "//localhost", root)
    for url in dict.fromkeys([root, from_this_pc]):
        try:
            r = await c.get(f"{url}/health", timeout=5)
        except httpx.HTTPError:
            continue
        if r.status_code == 200:
            note = "" if url == root else f" (from this PC at {url}; the worker uses {root})"
            return "OK", f"Kokoro server is up{note}"
    return "FAIL", ("can't reach the Kokoro server. Start it: docker compose -f docker-compose.dev.yml "
                    "--profile local-tts up -d kokoro, then wait ~1 minute for it to load")


async def check_mongo() -> Result:
    if (m := _missing("MONGODB_URI")):
        return m
    from motor.motor_asyncio import AsyncIOMotorClient

    client: AsyncIOMotorClient = AsyncIOMotorClient(settings.MONGODB_URI, serverSelectionTimeoutMS=6000)
    try:
        await client.admin.command("ping")
        return "OK", f"connected (database '{settings.MONGODB_DB_NAME}')"
    except Exception as e:
        hint = "check the password, and Atlas → Network Access must allow your IP" if "uth" not in str(e)[:60] \
            else "authentication failed: check the user and password in MONGODB_URI"
        return "FAIL", f"{type(e).__name__}: {hint}"
    finally:
        client.close()


async def check_livekit() -> Result:
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        if (m := _missing(name)):
            return m[0], f"{name} {m[1]}"
    from livekit import api
    from livekit.protocol import sip

    try:
        async with api.LiveKitAPI(settings.LIVEKIT_URL, settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET) as lk:
            await lk.room.list_rooms(api.ListRoomsRequest())
            trunks = (await lk.sip.list_inbound_trunk(sip.ListSIPInboundTrunkRequest())).items
        return "OK", f"project reachable; {len(trunks)} inbound SIP trunk(s)"
    except Exception as e:
        return "FAIL", f"{type(e).__name__}: URL, key and secret must come from the same LiveKit project"


async def check_livekit_number() -> Result:
    """With TELEPHONY_PROVIDER=livekit: is the dispatch rule for the rented number in place?"""
    from livekit import api
    from livekit.protocol import sip

    name = f"receptionist-{settings.APP_ENV}-number"
    try:
        async with api.LiveKitAPI(settings.LIVEKIT_URL, settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET) as lk:
            rules = (await lk.sip.list_dispatch_rule(sip.ListSIPDispatchRuleRequest())).items
    except Exception as e:
        return "FAIL", f"{type(e).__name__} while listing dispatch rules"
    if any(r.name == name for r in rules):
        return "OK", f"dispatch rule '{name}' exists. Assign it to your number: LiveKit Cloud → Telephony → Phone Numbers → ⋮"
    return "WARN", "dispatch rule not created yet: run uv run python -m scripts.setup_telephony"


def check_softphone() -> Result:
    bad = [n for n in ("SOFTPHONE_SIP_USERNAME", "SOFTPHONE_SIP_PASSWORD") if _missing(n)]
    if bad:
        return "FAIL", f"{', '.join(bad)} not set (needed for Linphone calls)"
    if not settings.softphone_numbers:
        return "FAIL", "SOFTPHONE_TEST_NUMBERS is empty"
    if re.search(r"[#\s\"']", settings.SOFTPHONE_SIP_PASSWORD):
        return "WARN", "password contains # space or quotes, which .env/Docker may cut. Use letters and digits"
    return "OK", f"{len(settings.softphone_numbers)} test number(s), credentials set"


async def check_twilio(c: httpx.AsyncClient) -> Result:
    for name in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER", "TWILIO_TERMINATION_URI"):
        if (m := _missing(name)):
            return m[0], f"{name} {m[1]}"
    r = await c.get(f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}.json",
                    auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN))
    return _http_result(r.status_code, "Twilio")


def check_secret_key() -> Result:
    if (m := _missing("SECRET_KEY")):
        return m
    if len(settings.SECRET_KEY) < 32:
        return "WARN", "shorter than 32 characters (production refuses to boot)"
    return "OK", "set"


async def main() -> None:
    print(f"APP_ENV={settings.APP_ENV}  telephony={settings.telephony_provider}\n")
    async with httpx.AsyncClient(timeout=10) as c:
        checks: list[tuple[str, Awaitable[Result] | Callable[[], Result]]] = [
            ("SECRET_KEY", check_secret_key),
            ("MongoDB", check_mongo()),
            ("OpenAI", check_openai(c)),
            ("Deepgram", check_deepgram(c)),
            ("ElevenLabs", check_elevenlabs(c)),
            ("Cartesia", check_cartesia(c)),
            ("Kokoro", check_kokoro(c)),
            ("Voice", check_voice_choice),
            ("LiveKit", check_livekit()),
        ]
        if settings.telephony_provider == "softphone":
            checks.append(("Softphone", check_softphone))
        elif settings.telephony_provider == "livekit":
            checks.append(("LK number", check_livekit_number()))
        else:
            checks.append(("Twilio", check_twilio(c)))
        results: list[Result] = []
        for name, job in checks:
            try:
                res = job() if callable(job) else await job
            except httpx.TimeoutException:
                res = ("WARN", "timed out reaching the service (network, VPN or firewall?). Run the check again")
            except Exception as e:
                res = ("FAIL", f"{type(e).__name__} while checking")
            results.append(res)
            icon = {"OK": "✅", "WARN": "⚠️ ", "FAIL": "❌"}[res[0]]
            print(f"{icon} {name:<11} {res[1]}")
    fails = sum(r[0] == "FAIL" for r in results)
    print(f"\n{fails} problem(s)." if fails else "\nAll required settings work.")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    asyncio.run(main())
