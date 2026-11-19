"""
Stripe over its REST API (Roadmap Task 5.3) — the handful of calls this product needs, with httpx
(already a dependency) instead of the SDK.

    customer   = await create_customer(email=…, name=…, tenant_id=…)
    session    = await create_checkout_session(…)      # signup / change plan / reactivate
    portal     = await create_portal_session(customer_id, return_url)
    await send_meter_event(customer_id, minutes, identifier=…)   # usage-based overage
    event      = verify_webhook(raw_body, request.headers["Stripe-Signature"])

No API version is pinned: the account's default applies, and every reader of a Stripe object here
copes with both the pre- and post-2025 shapes (subscription periods moved from the subscription to
its items in 2025-03-31). ⚠ VERIFY against the Stripe docs when you upgrade the account's API version.

Every write that could be retried sends an Idempotency-Key, so a retried request never creates a
second customer or session.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlencode

import httpx

from config import settings

logger = logging.getLogger(__name__)

API_BASE = "https://api.stripe.com/v1"
TIMEOUT_S = 20.0
SIGNATURE_TOLERANCE_S = 300


class StripeError(Exception):
    def __init__(self, message: str, *, status: int = 0, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class SignatureError(Exception):
    pass


def configured() -> bool:
    return settings.billing_enabled


def encode(params: Dict[str, Any], prefix: str = "") -> List[Tuple[str, str]]:
    """Stripe's form encoding: {"a": {"b": [1, 2]}} → a[b][0]=1&a[b][1]=2. None values are skipped."""
    out: List[Tuple[str, str]] = []
    for key, value in params.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if value is None:
            continue
        if isinstance(value, dict):
            out.extend(encode(value, name))
        elif isinstance(value, (list, tuple)):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    out.extend(encode(item, f"{name}[{i}]"))
                else:
                    out.append((f"{name}[{i}]", _scalar(item)))
        else:
            out.append((name, _scalar(value)))
    return out


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


async def request(method: str, path: str, params: Optional[Dict[str, Any]] = None, *,
                  idempotency_key: Optional[str] = None) -> Dict[str, Any]:
    if not configured():
        raise StripeError("Billing is not configured (STRIPE_SECRET_KEY)", status=503)
    headers = {"Authorization": f"Bearer {settings.STRIPE_SECRET_KEY.strip()}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key[:255]
    data = encode(params or {})
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_S, connect=5.0)) as client:
            if method.upper() in ("GET", "DELETE") and not data:
                resp = await client.request(method.upper(), f"{API_BASE}{path}", headers=headers)
            elif method.upper() == "GET":
                resp = await client.get(f"{API_BASE}{path}?{urlencode(data)}", headers=headers)
            else:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                resp = await client.request(method.upper(), f"{API_BASE}{path}", content=urlencode(data),
                                            headers=headers)
    except httpx.HTTPError as exc:
        raise StripeError(f"Stripe unreachable: {type(exc).__name__}", status=502) from exc
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code >= 400:
        err = (body or {}).get("error") or {}
        logger.warning("Stripe %s %s failed: HTTP %s %s", method, path, resp.status_code, err.get("code") or "")
        raise StripeError(err.get("message") or f"Stripe HTTP {resp.status_code}", status=resp.status_code,
                          code=err.get("code") or err.get("type") or "")
    return body


# ── Objects ──────────────────────────────────────────────────────────────────

async def create_customer(*, email: str, name: str, tenant_id: str) -> Dict[str, Any]:
    return await request("POST", "/customers", {
        "email": email, "name": name[:250], "metadata": {"tenant_id": tenant_id},
    }, idempotency_key=f"customer:{tenant_id}")


async def create_checkout_session(*, customer_id: str, tenant_id: str, price: str, success_url: str,
                                  cancel_url: str, trial_days: int = 0, overage_price: str = "",
                                  setup_fee_price: str = "", attempt: str = "",
                                  currency: str = "USD") -> Dict[str, Any]:
    """A subscription Checkout. The trial still requires a card (Task 5.2: abuse prevention).
    `currency`: charge in one of the Prices' `currency_options` (GBP, EUR, AUD) instead of USD.
    ⚠ VERIFY: every Price used here (plan, overage, setup fee) needs that currency option."""
    line_items: List[Dict[str, Any]] = [{"price": price, "quantity": 1}]
    if overage_price:
        line_items.append({"price": overage_price})          # metered: no quantity
    if setup_fee_price:
        line_items.append({"price": setup_fee_price, "quantity": 1})
    subscription_data: Dict[str, Any] = {"metadata": {"tenant_id": tenant_id}}
    if trial_days > 0:
        subscription_data["trial_period_days"] = trial_days
        subscription_data["trial_settings"] = {"end_behavior": {"missing_payment_method": "cancel"}}
    params: Dict[str, Any] = {
        "mode": "subscription", "customer": customer_id, "client_reference_id": tenant_id,
        "line_items": line_items, "subscription_data": subscription_data,
        "payment_method_collection": "always", "allow_promotion_codes": True,
        "billing_address_collection": "required", "metadata": {"tenant_id": tenant_id},
        "success_url": success_url, "cancel_url": cancel_url,
        "customer_update": {"address": "auto", "name": "auto"},
        "tax_id_collection": {"enabled": True},
    }
    if currency and currency.upper() != "USD":
        params["currency"] = currency.lower()
    if settings.STRIPE_AUTOMATIC_TAX:
        params["automatic_tax"] = {"enabled": True}
    key = f"checkout:{tenant_id}:{price}:{currency}:{trial_days}:{attempt}" if attempt else None
    return await request("POST", "/checkout/sessions", params, idempotency_key=key)


async def create_portal_session(customer_id: str, return_url: str) -> Dict[str, Any]:
    return await request("POST", "/billing_portal/sessions", {"customer": customer_id, "return_url": return_url})


async def retrieve_subscription(subscription_id: str) -> Dict[str, Any]:
    return await request("GET", f"/subscriptions/{subscription_id}")


async def send_meter_event(customer_id: str, value: int, *, identifier: str,
                           timestamp: Optional[datetime] = None) -> Dict[str, Any]:
    """One usage report. Stripe drops a second event with the same `identifier`, which is what makes
    a retried report safe (see core/billing/metering.py)."""
    ts = int((timestamp or datetime.now(timezone.utc)).timestamp())
    return await request("POST", "/billing/meter_events", {
        "event_name": settings.STRIPE_METER_EVENT_NAME, "identifier": identifier[:100], "timestamp": ts,
        "payload": {"stripe_customer_id": customer_id, "value": str(int(value))},
    })


# ── Reading subscriptions (both API shapes) ─────────────────────────────────

def _ts(value: Any) -> Optional[datetime]:
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    return None


def subscription_items(sub: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(((sub.get("items") or {}).get("data")) or [])


def subscription_prices(sub: Dict[str, Any]) -> Iterable[str]:
    for item in subscription_items(sub):
        price = item.get("price") or {}
        pid = price.get("id") if isinstance(price, dict) else price
        if pid:
            yield str(pid)


def subscription_period(sub: Dict[str, Any]) -> Tuple[Optional[datetime], Optional[datetime]]:
    start, end = sub.get("current_period_start"), sub.get("current_period_end")
    if not start:
        for item in subscription_items(sub):
            if item.get("current_period_start"):
                start, end = item.get("current_period_start"), item.get("current_period_end")
                break
    return _ts(start), _ts(end)


def subscription_trial_end(sub: Dict[str, Any]) -> Optional[datetime]:
    return _ts(sub.get("trial_end"))


# ── Webhooks ─────────────────────────────────────────────────────────────────

def verify_webhook(payload: bytes, header: str, secret: Optional[str] = None, *,
                   tolerance_s: int = SIGNATURE_TOLERANCE_S, now: Optional[float] = None) -> Dict[str, Any]:
    """Check `Stripe-Signature` (t=…,v1=…) over the raw body and return the parsed event."""
    secret = (secret if secret is not None else settings.STRIPE_WEBHOOK_SECRET).strip()
    if not secret:
        raise SignatureError("STRIPE_WEBHOOK_SECRET is not set")
    timestamp, signatures = None, []
    for part in (header or "").split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            signatures.append(value)
    if not timestamp or not signatures:
        raise SignatureError("malformed Stripe-Signature header")
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise SignatureError("bad timestamp") from exc
    if abs((now if now is not None else time.time()) - ts) > tolerance_s:
        raise SignatureError("timestamp outside tolerance (replayed or clock skew)")
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in signatures):
        raise SignatureError("signature mismatch")
    try:
        event = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise SignatureError("body is not JSON") from exc
    if not isinstance(event, dict) or not event.get("id") or not event.get("type"):
        raise SignatureError("not a Stripe event")
    return event


def sign_for_tests(payload: bytes, secret: str, timestamp: Optional[int] = None) -> str:
    """The header Stripe would send (tests and local replay)."""
    t = str(timestamp if timestamp is not None else int(time.time()))
    sig = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={sig}"
