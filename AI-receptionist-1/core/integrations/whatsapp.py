"""
One WhatsApp sender (Roadmap Task 2.9) — replaces the three copies in meta_handler, reminders
and the hotel tools. Meta Cloud API; media helpers stay in whatsapp/meta_handler.py.

Free-form text is delivered only inside the 24-hour customer-service window (24 h after the
customer last messaged the business); outside it, Meta requires an approved template.

`send_message` / `send_template` return a `SendResult` carrying Meta's error in plain English;
`send_text` keeps the old bool contract for callers that only care whether it went.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx

from config import settings
from core.phone import digits, mask_phone

logger = logging.getLogger(__name__)

CHUNK = 1500
WINDOW_HOURS = 24
TEMPLATE_CACHE_S = 300

# Meta error codes we see in practice → what the owner should do about it.
ERROR_HINTS: Dict[int, str] = {
    131047: "The patient hasn't messaged you in the last 24 hours, so WhatsApp only allows an approved template.",
    131030: "This number isn't on the allowed recipient list of your WhatsApp test number. Add it in Meta's "
            "WhatsApp Manager, or connect a live business number.",
    131026: "This number can't receive WhatsApp messages (it may not be on WhatsApp).",
    131051: "WhatsApp doesn't support that message type.",
    131056: "Too many messages to this number in a short time. Wait a minute and try again.",
    132000: "The template's placeholders don't match — fill in every field.",
    132001: "That template doesn't exist in this language or isn't approved yet.",
    132015: "That template is paused by Meta because of low quality.",
    132016: "That template has been disabled by Meta.",
    190: "The WhatsApp access token has expired or is invalid. The platform admin needs to renew it.",
    100: "Meta rejected the request (bad parameters or phone number ID).",
    10: "The WhatsApp app doesn't have permission to send from this number.",
    368: "Meta has temporarily blocked sending from this account for policy reasons.",
}


@dataclass
class SendResult:
    ok: bool
    message_id: Optional[str] = None
    error_code: Optional[int] = None
    error: Optional[str] = None


def base_url() -> str:
    version = (settings.META_GRAPH_API_VERSION or "v20.0").strip()
    return f"https://graph.facebook.com/{version}"


def chunk(message: str, size: int = CHUNK) -> List[str]:
    message = message or ""
    if len(message) <= size:
        return [message]
    parts: List[str] = []
    while message:
        cut = message.rfind("\n", 0, size) if len(message) > size else len(message)
        if cut <= 0:
            cut = size
        parts.append(message[:cut].rstrip())
        message = message[cut:].lstrip()
    return [p for p in parts if p]


def _token() -> str:
    return (settings.META_WA_ACCESS_TOKEN or "").strip()


def sender_id(phone_number_id: Optional[str] = None) -> str:
    return (phone_number_id or settings.META_WA_PHONE_NUMBER_ID or "").strip()


def account_id(waba_id: Optional[str] = None) -> str:
    return (waba_id or settings.META_WABA_ID or "").strip()


def configured(phone_number_id: Optional[str] = None) -> bool:
    return bool(_token()) and bool(sender_id(phone_number_id))


def explain(code: Optional[int], fallback: str = "") -> str:
    if code is not None and code in ERROR_HINTS:
        return ERROR_HINTS[code]
    return fallback or "WhatsApp send failed."


def parse_error(resp: httpx.Response) -> Tuple[Optional[int], str]:
    """Meta's `{"error": {"code", "message", "error_data": {"details"}}}` → (code, plain English)."""
    try:
        err = (resp.json() or {}).get("error") or {}
    except Exception:
        err = {}
    code = err.get("code")
    code = int(code) if isinstance(code, (int, str)) and str(code).isdigit() else None
    detail = ((err.get("error_data") or {}).get("details") or err.get("message") or "").strip()
    return code, explain(code, f"WhatsApp send failed (HTTP {resp.status_code}){': ' + detail if detail else ''}.")


async def send_message(to: str, payload: Dict[str, Any], phone_number_id: Optional[str] = None) -> SendResult:
    """POST one message (`payload` = the type-specific part, e.g. {"type": "text", "text": {...}})."""
    token, pnid = _token(), sender_id(phone_number_id)
    if not token or not pnid:
        return SendResult(False, error="WhatsApp isn't set up on the server (access token or phone number ID missing).")
    dest = digits(to)
    if len(dest) < 7:
        return SendResult(False, error="That phone number looks incomplete — include the country code.")
    body = {"messaging_product": "whatsapp", "to": dest, **payload}
    from core.resilience import breaker

    circuit = breaker("whatsapp")                   # audit SRE-08: Meta down → fail fast, don't queue up 20 s waits
    if not circuit.allow():
        return SendResult(False, error="WhatsApp isn't answering right now. Try again in a minute.")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(f"{base_url()}/{pnid}/messages", json=body,
                                     headers={"Authorization": f"Bearer {token}"})
    except Exception:
        circuit.failure()
        logger.error("WhatsApp send to %s errored", mask_phone(to), exc_info=True)
        return SendResult(False, error="Couldn't reach WhatsApp. Try again in a moment.")
    if resp.status_code >= 500:
        circuit.failure()
    else:
        circuit.success()
    if resp.status_code >= 400:
        code, msg = parse_error(resp)
        logger.error("WhatsApp send to %s failed: HTTP %s code=%s", mask_phone(to), resp.status_code, code)
        return SendResult(False, error_code=code, error=msg)
    try:
        message_id = ((resp.json() or {}).get("messages") or [{}])[0].get("id")
    except Exception:
        message_id = None
    logger.info("WhatsApp sent to %s", mask_phone(to))
    return SendResult(True, message_id=message_id)


async def send_text_result(to: str, message: str, phone_number_id: Optional[str] = None) -> SendResult:
    """Free-form text, split into WhatsApp-sized chunks. The last chunk's id is returned."""
    result = SendResult(False, error="Empty message.")
    for part in chunk(message):
        result = await send_message(to, {"type": "text", "text": {"body": part}}, phone_number_id)
        if not result.ok:
            return result
    return result


async def send_text(to: str, message: str, phone_number_id: Optional[str] = None) -> bool:
    """Send a text message from the given business number (or the platform default)."""
    if not configured(phone_number_id):
        logger.warning("WhatsApp send skipped: token or phone_number_id missing")
        return False
    return (await send_text_result(to, message, phone_number_id)).ok


async def send_template(to: str, name: str, language: str, body_params: List[str],
                        phone_number_id: Optional[str] = None) -> SendResult:
    template: Dict[str, Any] = {"name": name, "language": {"code": language}}
    if body_params:
        template["components"] = [{"type": "body",
                                   "parameters": [{"type": "text", "text": p} for p in body_params]}]
    return await send_message(to, {"type": "template", "template": template}, phone_number_id)


# ── Templates ────────────────────────────────────────────────────────────────

PLACEHOLDER = re.compile(r"\{\{\s*(\d+)\s*\}\}")
_template_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}


def _summarise_template(t: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    components = t.get("components") or []
    body = next((c.get("text", "") for c in components if c.get("type") == "BODY"), "")
    # Header/button parameters aren't supported from the dashboard — offer only templates we can fill.
    if any(c.get("type") == "HEADER" and c.get("format") not in (None, "TEXT") for c in components):
        return None
    header = next((c for c in components if c.get("type") == "HEADER"), None)
    if header and PLACEHOLDER.search(header.get("text", "")):
        return None
    params = [int(n) for n in PLACEHOLDER.findall(body)]
    return {"name": t.get("name"), "language": t.get("language"), "category": t.get("category"),
            "body": body, "params": max(params, default=0)}


async def list_templates(waba_id: Optional[str] = None, *, fresh: bool = False) -> List[Dict[str, Any]]:
    """Approved templates on the WhatsApp Business Account. Raises RuntimeError with a plain message."""
    waba, token = account_id(waba_id), _token()
    if not waba or not token:
        raise RuntimeError("WhatsApp templates need META_WABA_ID and an access token on the server.")
    cached = _template_cache.get(waba)
    if cached and not fresh and time.monotonic() - cached[0] < TEMPLATE_CACHE_S:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{base_url()}/{waba}/message_templates",
                                    params={"status": "APPROVED", "limit": 200,
                                            "fields": "name,language,category,components,status"},
                                    headers={"Authorization": f"Bearer {token}"})
    except Exception:
        logger.warning("WhatsApp template list errored", exc_info=True)
        raise RuntimeError("Couldn't reach WhatsApp to load templates.")
    if resp.status_code >= 400:
        _, msg = parse_error(resp)
        raise RuntimeError(msg)
    rows = [_summarise_template(t) for t in (resp.json() or {}).get("data", []) if t.get("status", "APPROVED") == "APPROVED"]
    templates = sorted((r for r in rows if r), key=lambda r: (r["name"], r["language"]))
    _template_cache[waba] = (time.monotonic(), templates)
    return templates


def clear_template_cache() -> None:
    _template_cache.clear()
