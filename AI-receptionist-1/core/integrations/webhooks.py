"""
Outgoing webhooks (Roadmap Task 2.8, the Zapier/Make "escape hatch").

    emit(tenant, "appointment.created", {...})     # fire-and-forget, never blocks a call

Every tenant endpoint that wants the event gets one POST with a JSON body:

    {"id": "evt_…", "event": "appointment.created", "created_at": "…", "tenant_id": "ten_…", "data": {…}}

and these headers, so the receiver can prove the request came from us and is fresh:

    X-Receptionist-Event:      appointment.created
    X-Receptionist-Delivery:   evt_…
    X-Receptionist-Timestamp:  1700000000
    X-Receptionist-Signature:  v1=<hex HMAC-SHA256 of "<timestamp>.<body>" with the endpoint secret>

Deliveries time out after 10 s and retry once on a network error or 5xx. The outcome is stored on
the endpoint (`last_delivery`) so the dashboard can show "last delivered 2 min ago / failed 500".
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from core.tenancy.ids import new_id
from core.tenancy.models import WEBHOOK_EVENTS, TenantConfig, WebhookEndpoint
from utils.serialize import to_jsonable

logger = logging.getLogger(__name__)

TIMEOUT_S = 10.0
RETRIES = 1
USER_AGENT = "AI-Receptionist-Webhooks/1.0"

__all__ = ["WEBHOOK_EVENTS", "emit", "deliver", "sign", "verify", "endpoints_for", "new_secret"]


def new_secret() -> str:
    return "whsec_" + hashlib.sha256(f"{time.time_ns()}{new_id('s')}".encode()).hexdigest()[:40]


def sign(secret: str, timestamp: str, body: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), f"{timestamp}.{body}".encode("utf-8"), hashlib.sha256)
    return "v1=" + mac.hexdigest()


def verify(secret: str, timestamp: str, body: str, signature: str, *, tolerance_s: int = 300,
           now: Optional[float] = None) -> bool:
    """For receivers (and our tests): constant-time check plus a replay window."""
    try:
        age = abs((now if now is not None else time.time()) - int(timestamp))
    except (TypeError, ValueError):
        return False
    if age > tolerance_s:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature or "")


def endpoints_for(t: TenantConfig, event: str) -> List[WebhookEndpoint]:
    return [e for e in t.integrations.webhooks if e.wants(event)]


def envelope(t: TenantConfig, event: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": new_id("evt"), "event": event, "created_at": datetime.now(timezone.utc).isoformat(),
            "tenant_id": t.id, "data": to_jsonable(data)}


async def _post(url: str, body: str, headers: Dict[str, str]) -> int:
    async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False, headers={"User-Agent": USER_AGENT}) as client:
        resp = await client.post(url, content=body, headers=headers)
        return resp.status_code


async def deliver(endpoint: WebhookEndpoint, payload: Dict[str, Any]) -> Dict[str, Any]:
    """POST one event to one endpoint. Returns {"ok", "status", "attempts"}; never raises."""
    body = json.dumps(payload, separators=(",", ":"), default=str)
    ts = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Receptionist-Event": payload["event"],
        "X-Receptionist-Delivery": payload["id"],
        "X-Receptionist-Timestamp": ts,
    }
    if endpoint.secret:
        headers["X-Receptionist-Signature"] = sign(endpoint.secret, ts, body)
    status: Optional[int] = None
    attempts = 0
    for attempt in range(RETRIES + 1):
        attempts = attempt + 1
        try:
            status = await _post(endpoint.url, body, headers)
        except Exception as e:           # DNS, timeout, refused…
            logger.warning("Webhook %s → %s failed (%s), attempt %d", payload["event"], endpoint.id,
                           type(e).__name__, attempts)
            status = None
        if status is not None and status < 500:
            break
        if attempt < RETRIES:
            await asyncio.sleep(1.0)
    ok = status is not None and 200 <= status < 300
    if not ok:
        logger.warning("Webhook %s → %s not delivered (HTTP %s)", payload["event"], endpoint.id, status)
    return {"ok": ok, "status": status, "attempts": attempts}


async def _record(tenant_id: str, endpoint_id: str, event: str, result: Dict[str, Any]) -> None:
    try:
        from database.mongodb import get_db

        await get_db().tenants.update_one(
            {"_id": tenant_id, "integrations.webhooks.id": endpoint_id},
            {"$set": {"integrations.webhooks.$.last_delivery": {
                "at": datetime.now(timezone.utc), "event": event, "ok": result["ok"], "status": result["status"]}}})
    except Exception:
        logger.debug("Recording webhook delivery failed", exc_info=True)


async def send(t: TenantConfig, endpoint: WebhookEndpoint, event: str, data: Dict[str, Any]) -> Dict[str, Any]:
    result = await deliver(endpoint, envelope(t, event, data))
    await _record(t.id, endpoint.id, event, result)
    return result


def emit(t: Optional[TenantConfig], event: str, data: Dict[str, Any]) -> int:
    """Queue `event` for every endpoint of the tenant that wants it. Returns how many were queued."""
    if t is None or t.is_legacy or event not in WEBHOOK_EVENTS:
        return 0
    targets = endpoints_for(t, event)
    if not targets:
        return 0
    from core.notifications import fire     # lazy: notifications imports the other integrations

    for endpoint in targets:
        fire(send(t, endpoint, event, data))
    return len(targets)
