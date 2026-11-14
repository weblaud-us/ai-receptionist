"""
Google Calendar (Roadmap Task 2.8) — OAuth 2.0, free/busy, and writing bookings as events.

    url = auth_url(tenant_id, user_id)          # owner clicks "Connect Google Calendar"
    tokens = await exchange_code(code)           # /integrations/google/callback
    busy = await freebusy(refresh_token, ["primary"], start, end)
    event_id = await insert_event(refresh_token, "primary", summary=..., start=..., end=...)

Calls are never made inside a live call: availability reads busy times cached by the sync job
(core/services/calendar_sync.py). Refresh tokens are stored encrypted (core/crypto.py).

⚠ VERIFY before production: Google requires OAuth app verification for calendar scopes (can take
weeks — start early), and scope names/classifications change. Redirect URI to register:
{WEBHOOK_BASE_URL}/integrations/google/callback
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import httpx

from config import settings

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/calendar/v3"
SCOPES = ("https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/calendar.freebusy")
STATE_TTL_S = 15 * 60

# In-process access-token cache: refresh-token hash → (access token, expiry epoch)
_access: Dict[str, Tuple[str, float]] = {}


class GoogleError(Exception):
    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


def configured() -> bool:
    return bool(settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET)


def redirect_uri() -> str:
    return f"{settings.WEBHOOK_BASE_URL.rstrip('/')}/integrations/google/callback"


# ── OAuth state: signed, expiring, names the tenant and the user who connected ──

def _b64(raw: bytes) -> str:
    return urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _sig(payload: str) -> str:
    return _b64(hmac.new(settings.SECRET_KEY.encode(), f"google-oauth:{payload}".encode(), hashlib.sha256).digest())


def make_state(tenant_id: str, user_id: str, now: Optional[float] = None) -> str:
    payload = _b64(json.dumps({"t": tenant_id, "u": user_id, "exp": int((now or time.time()) + STATE_TTL_S)}).encode())
    return f"{payload}.{_sig(payload)}"


def read_state(state: str, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    try:
        payload, sig = state.split(".", 1)
        if not hmac.compare_digest(sig, _sig(payload)):
            return None
        data = json.loads(urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if int(data["exp"]) < int(now or time.time()):
            return None
        return {"tenant_id": data["t"], "user_id": data["u"]}
    except Exception:
        return None


def auth_url(tenant_id: str, user_id: str) -> str:
    return AUTH_ENDPOINT + "?" + urlencode({
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",           # we need a refresh token
        "prompt": "consent",                # …every time, or Google omits it on re-connect
        "include_granted_scopes": "true",
        "state": make_state(tenant_id, user_id),
    })


async def _post_token(data: Dict[str, str]) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(TOKEN_ENDPOINT, data={
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET, **data})
    if resp.status_code >= 400:
        raise GoogleError(f"token endpoint HTTP {resp.status_code}", resp.status_code)
    return resp.json()


async def exchange_code(code: str) -> Dict[str, Any]:
    tokens = await _post_token({"code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri()})
    if not tokens.get("refresh_token"):
        raise GoogleError("Google returned no refresh token — remove the app's access in your Google account and connect again")
    return tokens


async def access_token(refresh_token: str) -> str:
    key = hashlib.sha256(refresh_token.encode()).hexdigest()
    hit = _access.get(key)
    if hit and hit[1] - 60 > time.time():
        return hit[0]
    tokens = await _post_token({"refresh_token": refresh_token, "grant_type": "refresh_token"})
    token = tokens["access_token"]
    _access[key] = (token, time.time() + int(tokens.get("expires_in", 3600)))
    return token


async def _api(refresh_token: str, method: str, path: str, *, json_body: Any = None, params: Any = None) -> Any:
    token = await access_token(refresh_token)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.request(method, f"{API}{path}", json=json_body, params=params,
                                    headers={"Authorization": f"Bearer {token}"})
    if resp.status_code == 404 and method == "DELETE":
        return None                                   # already gone
    if resp.status_code >= 400:
        raise GoogleError(f"Calendar API {method} {path.split('?')[0]} → HTTP {resp.status_code}", resp.status_code)
    return resp.json() if resp.content else None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


async def freebusy(refresh_token: str, calendar_ids: List[str], start: datetime, end: datetime
                   ) -> Dict[str, List[Tuple[datetime, datetime]]]:
    body = {"timeMin": _iso(start), "timeMax": _iso(end), "items": [{"id": c} for c in calendar_ids]}
    data = await _api(refresh_token, "POST", "/freeBusy", json_body=body)
    out: Dict[str, List[Tuple[datetime, datetime]]] = {}
    for cal_id, info in (data or {}).get("calendars", {}).items():
        if info.get("errors"):
            raise GoogleError(f"calendar {cal_id}: {info['errors'][0].get('reason', 'error')}")
        out[cal_id] = [(_parse(b["start"]), _parse(b["end"])) for b in info.get("busy", [])]
    return out


async def insert_event(refresh_token: str, calendar_id: str, *, summary: str, start: datetime, end: datetime,
                       description: str = "", timezone_name: str = "UTC") -> str:
    body = {"summary": summary[:200], "description": description[:2000],
            "start": {"dateTime": _iso(start), "timeZone": timezone_name},
            "end": {"dateTime": _iso(end), "timeZone": timezone_name},
            "source": {"title": "AI receptionist", "url": settings.DASHBOARD_URL or "https://example.com"}}
    data = await _api(refresh_token, "POST", f"/calendars/{calendar_id}/events", json_body=body)
    return str(data["id"])


async def delete_event(refresh_token: str, calendar_id: str, event_id: str) -> None:
    await _api(refresh_token, "DELETE", f"/calendars/{calendar_id}/events/{event_id}")
