"""
Phone number provisioning (Roadmap Task 5.4).

    results = await search("US", area_code="512")
    order   = await start_order(db, tenant, "+15125550123", requested_by=user_id)
    order   = await run_order(db, order_id)      # the job; safe to run again from any point
    await release(db, "+15125550123")            # offboarding

An order walks these steps, each recorded on the `number_orders` document as it completes, and
each safe to repeat (the job is retried with backoff and can be re-run by a platform admin):

| Step       | What                                                                 | Idempotent because          |
|------------|----------------------------------------------------------------------|-----------------------------|
| purchased  | Twilio IncomingPhoneNumbers.create                                   | looks the number up first   |
| trunk      | attach to the Elastic SIP trunk (TWILIO_SIP_TRUNK_SID)               | "already attached" = done   |
| livekit    | add to the LiveKit inbound trunk's `numbers` (LIVEKIT_SIP_INBOUND_TRUNK_ID) | ListUpdate add is a set op |
| mapped     | phone_numbers → tenant (status "pending")                            | upsert on e164              |
| verified   | synthetic call through the phone network hears the greeting → active | re-dials; skipped if off    |

Only US / CA numbers are instant (NUMBER_INSTANT_COUNTRIES). UK, EU and UAE numbers need a
regulatory bundle approved by Twilio first; those orders stop at "needs_documents" and you finish
them from the platform page once the bundle is approved. ⚠ VERIFY current Twilio requirements.

Development (TELEPHONY_PROVIDER=softphone): search offers fake +1 555 numbers and an order only
maps the number, so the whole wizard can be clicked through on a laptop.
"""

from __future__ import annotations

import logging
import random
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from config import settings
from core.phone import digits, mask_phone
from core.repositories import Repos
from core.repositories.repos import PhoneNumbersStore
from core.tenancy import invalidate
from core.tenancy.ids import new_id
from core.tenancy.models import TenantConfig

logger = logging.getLogger(__name__)

TWILIO_API = "{base}/2010-04-01/Accounts/{sid}"   # base: settings.twilio_api_base (edge / region)
TRUNKING_API = "https://trunking.twilio.com/v1/Trunks/{trunk}/PhoneNumbers"
TIMEOUT_S = 20.0
STEPS = ("purchased", "trunk", "livekit", "mapped", "verified")
OPEN_STATUSES = ("pending", "running", "failed", "needs_documents", "verify_failed")


class ProvisioningError(Exception):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def mode() -> str:
    """twilio | fake | off. Fake = development softphone; LiveKit-rented numbers are assigned by hand."""
    if not settings.NUMBER_PROVISIONING_ENABLED:
        return "off"
    provider = settings.telephony_provider
    if provider == "softphone":
        return "fake"
    if provider == "twilio" and settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN:
        return "twilio"
    return "off"


def setup_problems() -> List[str]:
    """What's missing for automatic numbers, in words the platform page can show. Empty = ready."""
    problems: List[str] = []
    if not settings.NUMBER_PROVISIONING_ENABLED:
        problems.append("Set NUMBER_PROVISIONING_ENABLED=true")
    if settings.telephony_provider == "twilio":
        if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN):
            problems.append("Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN")
        if not settings.TWILIO_SIP_TRUNK_SID:
            problems.append("Set TWILIO_SIP_TRUNK_SID (Twilio → Elastic SIP Trunking → your trunk → Trunk SID, TK…)")
        if not settings.LIVEKIT_SIP_INBOUND_TRUNK_ID:
            problems.append("Set LIVEKIT_SIP_INBOUND_TRUNK_ID (the ST_… id setup_telephony prints for the inbound trunk)")
    elif settings.telephony_provider not in ("softphone",):
        problems.append(f"Automatic numbers need Twilio (TELEPHONY_PROVIDER is {settings.telephony_provider})")
    return problems


def e164(raw: str) -> str:
    d = digits(raw)
    if len(d) < 8:
        raise ProvisioningError("Phone number must be in international format, e.g. +15125551234", status=422)
    return "+" + d


def _auth() -> tuple:
    return (settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


async def _twilio(method: str, url: str, **kw: Any) -> httpx.Response:
    async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_S, connect=5.0)) as client:
        return await client.request(method, url, auth=_auth(), **kw)


# ── Search ───────────────────────────────────────────────────────────────────

def _fake_numbers(area_code: str, limit: int) -> List[Dict[str, Any]]:
    rng = random.Random(area_code or "555")
    return [{"e164": f"+1555{rng.randint(1000000, 9999999)}", "locality": "Test number", "region": "DEV",
             "country": "US", "fake": True} for _ in range(limit)]


async def search(country: str, *, area_code: str = "", contains: str = "", limit: int = 10) -> List[Dict[str, Any]]:
    country = (country or "US").upper()
    m = mode()
    if m == "off":
        raise ProvisioningError("Self-serve numbers aren't available yet — use call forwarding, or contact us.",
                                status=503)
    if m == "fake":
        return _fake_numbers(area_code, limit)
    params: Dict[str, Any] = {"VoiceEnabled": "true", "PageSize": max(1, min(limit, 30))}
    if area_code:
        params["AreaCode"] = area_code
    if contains:
        params["Contains"] = contains
    url = f"{TWILIO_API.format(base=settings.twilio_api_base, sid=settings.TWILIO_ACCOUNT_SID)}/AvailablePhoneNumbers/{country}/Local.json"
    try:
        resp = await _twilio("GET", url, params=params)
    except httpx.HTTPError as exc:
        raise ProvisioningError("The number search is unavailable right now. Try again in a minute.", status=502) from exc
    if resp.status_code == 404:
        return []
    if resp.status_code >= 400:
        logger.warning("Twilio number search failed: HTTP %s", resp.status_code)
        raise ProvisioningError("The number search is unavailable right now. Try again in a minute.", status=502)
    return [{"e164": n.get("phone_number"), "locality": n.get("locality") or "", "region": n.get("region") or "",
             "country": n.get("iso_country") or country}
            for n in (resp.json() or {}).get("available_phone_numbers") or [] if n.get("phone_number")]


async def list_owned(db: Any, *, limit: int = 500) -> List[Dict[str, Any]]:
    """Numbers already in our Twilio account that no business is using (platform "use a number I own").
    Development (fake mode): the softphone test numbers."""
    m = mode()
    if m == "off":
        raise ProvisioningError("Number provisioning is off. Set NUMBER_PROVISIONING_ENABLED=true.", status=503)
    rows: List[Dict[str, Any]] = []
    if m == "fake":
        rows = [{"e164": n, "friendly_name": "Softphone test number", "sid": "fake", "country": "US"}
                for n in settings.softphone_numbers]
    else:
        url: Optional[str] = (f"{TWILIO_API.format(base=settings.twilio_api_base, sid=settings.TWILIO_ACCOUNT_SID)}"
                              "/IncomingPhoneNumbers.json?PageSize=100")
        while url and len(rows) < limit:
            try:
                resp = await _twilio("GET", url)
            except httpx.HTTPError as exc:
                raise ProvisioningError("Twilio is unavailable right now. Try again in a minute.", status=502) from exc
            if resp.status_code >= 400:
                raise ProvisioningError(f"Twilio number list failed (HTTP {resp.status_code})", status=502)
            body = resp.json() or {}
            for n in body.get("incoming_phone_numbers") or []:
                if n.get("phone_number") and (n.get("capabilities") or {}).get("voice", True):
                    rows.append({"e164": n["phone_number"], "friendly_name": n.get("friendly_name") or "",
                                 "sid": n.get("sid"), "country": _country_of(n["phone_number"])})
            nxt = body.get("next_page_uri")
            url = f"{settings.twilio_api_base}{nxt}" if nxt else None
    taken = {r["e164"] async for r in db.phone_numbers.find({"status": {"$ne": "released"}}, {"e164": 1})}
    taken |= {o["e164"] async for o in db.number_orders.find({"status": {"$in": list(OPEN_STATUSES)}}, {"e164": 1})}
    return [r for r in rows if r["e164"] not in taken][:limit]


async def run_order_in_background(order_id: str) -> None:
    """Queue the order on the job worker (retries with backoff); without Redis, run it in-process."""
    import asyncio

    from jobs.queue import enqueue

    if await enqueue("provision_number", order_id, job_id=f"provision:{order_id}"):
        return

    async def run() -> None:
        try:
            from database.mongodb import get_db

            await run_order(get_db(), order_id)
        except Exception:
            logger.warning("Number order %s failed (in-process run)", order_id)
    asyncio.create_task(run())


# ── Orders ───────────────────────────────────────────────────────────────────

async def active_number_count(db: Any, tenant_id: str) -> int:
    numbers = await db.phone_numbers.count_documents({"tenant_id": tenant_id, "status": {"$ne": "released"},
                                                      "purpose": {"$ne": "outbound_sales"}})
    orders = await db.number_orders.count_documents({"tenant_id": tenant_id, "status": {"$in": list(OPEN_STATUSES)}})
    return numbers + orders


async def start_order(db: Any, tenant: TenantConfig, number: str, *, requested_by: str,
                      documents: Optional[Dict[str, Any]] = None, enforce_limits: bool = True,
                      owned: bool = False) -> Dict[str, Any]:
    """`enforce_limits=False` (platform admin): skip the subscription and plan-limit checks.
    `owned=True`: a number already in our Twilio account — nothing is bought, and it never waits for a
    regulatory bundle (it passed the carrier's checks when it was bought)."""
    if mode() == "off":
        raise ProvisioningError("Self-serve numbers aren't available yet.", status=503)
    if enforce_limits and tenant.status not in ("trial", "active"):
        raise ProvisioningError("Your subscription needs to be active to add a number.", status=402)
    number = e164(number)
    if enforce_limits and tenant.limits.max_numbers and \
            await active_number_count(db, tenant.id) >= tenant.limits.max_numbers:
        raise ProvisioningError(f"Your plan includes {tenant.limits.max_numbers} number(s). Upgrade to add more.",
                                status=402)
    existing = await PhoneNumbersStore(db).get(number)
    if existing and existing.get("status") != "released" and not (owned and existing.get("tenant_id") == tenant.id):
        raise ProvisioningError("That number is already in use.", status=409)
    if owned and mode() == "twilio" and not await _find_owned(number):
        raise ProvisioningError("That number isn't in your Twilio account.", status=404)
    if await db.number_orders.find_one({"e164": number, "status": {"$in": list(OPEN_STATUSES)}}):
        raise ProvisioningError("That number is already being set up.", status=409)
    country = _country_of(number)
    order: Dict[str, Any] = {
        "_id": new_id("nmo"), "tenant_id": tenant.id, "e164": number, "country": country,
        "provider": "fake" if mode() == "fake" else "twilio", "steps": {}, "attempts": 0,
        "requested_by": requested_by, "created_at": utcnow(), "updated_at": utcnow(), "error": "",
        "status": "pending", "source": "owned" if owned else "buy",
    }
    if order["provider"] == "twilio" and not owned and country not in settings.number_instant_countries:
        order["status"] = "needs_documents"
        order["documents"] = documents or {}
        # AU (+61) and UK (+44) bundles name the registered business: ABN / Companies House number.
        order["legal_entity"] = tenant.legal_entity.model_dump()
        order["note"] = ("Numbers in this country need a regulatory bundle (business address proof, ID) approved "
                         "by the carrier first — usually 1–3 business days. We'll e-mail you when it's live. "
                         "Use call forwarding or browser testing meanwhile.")
    await db.number_orders.insert_one(order)
    if order["status"] == "needs_documents":
        await _alert_operator(tenant, order)
    await Repos(db, tenant.id).audit.record(user_id=requested_by, action="number.order", target_type="phone_number",
                                            target_id=number, diff={"status": order["status"]})
    return order


async def _alert_operator(tenant: TenantConfig, order: Dict[str, Any]) -> None:
    """Tell the platform team a number needs its regulatory bundle (AU, UK, EU…). Never raises."""
    to = settings.SUPPORT_EMAIL or settings.PILOT_REPORT_BCC
    if not to:
        logger.warning("Number order %s needs documents but SUPPORT_EMAIL is not set", order["_id"])
        return
    try:
        from core.integrations.email import send_email

        await send_email(to, f"[numbers] {tenant.name} needs a {order['country']} number",
                         f"{tenant.name} ({tenant.id}) ordered {order['e164']} ({order['country']}).\n"
                         f"{_entity_lines(tenant, order['country'])}\n"
                         "1. Create or reuse their regulatory bundle (and address) in the Twilio console.\n"
                         "2. When Twilio approves it, open the platform app → Numbers, paste the bundle SID and "
                         f"approve order {order['_id']}.\n")
    except Exception:
        logger.warning("Could not e-mail the number-order alert for %s", order["_id"], exc_info=True)


def _entity_lines(tenant: TenantConfig, country: str) -> str:
    """The registered business, for the bundle. Says what's missing so the operator can ask for it."""
    e = tenant.legal_entity
    lines = [f"Registered name: {e.registered_name or '(not given)'}"]
    if country == "AU":
        lines.append(f"ABN: {e.abn or '(missing: ask the business, Settings → Business)'}")
    elif country == "GB":
        lines.append(f"Companies House number: {e.company_number or '(not given; sole traders have none)'}")
    return "\n".join(lines) + "\n"


def _country_of(number: str) -> str:
    try:
        import phonenumbers

        region = phonenumbers.region_code_for_number(phonenumbers.parse(number, None))
        return region or "US"
    except Exception:
        return "US"


async def _step(db: Any, order: Dict[str, Any], name: str, value: Any) -> None:
    order["steps"][name] = value
    await db.number_orders.update_one({"_id": order["_id"]}, {"$set": {f"steps.{name}": value,
                                                                       "updated_at": utcnow()}})


async def _find_owned(number: str) -> Optional[str]:
    url = f"{TWILIO_API.format(base=settings.twilio_api_base, sid=settings.TWILIO_ACCOUNT_SID)}/IncomingPhoneNumbers.json"
    resp = await _twilio("GET", url, params={"PhoneNumber": number})
    if resp.status_code >= 400:
        raise ProvisioningError(f"Twilio lookup failed (HTTP {resp.status_code})", status=502)
    rows = (resp.json() or {}).get("incoming_phone_numbers") or []
    return rows[0]["sid"] if rows else None


async def _purchase(number: str, tenant_id: str, *, bundle_sid: str = "", address_sid: str = "") -> str:
    """Buy the number. Outside the instant countries Twilio needs the approved regulatory bundle
    (BundleSid) and usually an address (AddressSid) on the purchase itself."""
    sid = await _find_owned(number)
    if sid:
        return sid
    url = f"{TWILIO_API.format(base=settings.twilio_api_base, sid=settings.TWILIO_ACCOUNT_SID)}/IncomingPhoneNumbers.json"
    data = {"PhoneNumber": number, "FriendlyName": f"receptionist {tenant_id}"[:64]}
    if bundle_sid:
        data["BundleSid"] = bundle_sid
    if address_sid:
        data["AddressSid"] = address_sid
    resp = await _twilio("POST", url, data=data)
    if resp.status_code >= 400:
        detail = ((resp.json() if resp.content else {}) or {}).get("message", "")
        raise ProvisioningError(f"Could not buy the number: {detail or resp.status_code}", status=502)
    return str(resp.json()["sid"])


async def _attach_trunk(phone_sid: str) -> None:
    if not settings.TWILIO_SIP_TRUNK_SID:
        raise ProvisioningError("TWILIO_SIP_TRUNK_SID is not set", status=503)
    resp = await _twilio("POST", TRUNKING_API.format(trunk=settings.TWILIO_SIP_TRUNK_SID),
                         data={"PhoneNumberSid": phone_sid})
    if resp.status_code < 400:
        return
    body = (resp.json() if resp.content else {}) or {}
    if resp.status_code == 409 or "already" in str(body.get("message", "")).lower():
        return
    raise ProvisioningError(f"Could not attach the number to the SIP trunk (HTTP {resp.status_code})", status=502)


async def _livekit_numbers(*, add: Optional[List[str]] = None, remove: Optional[List[str]] = None) -> None:
    if not settings.LIVEKIT_SIP_INBOUND_TRUNK_ID:
        raise ProvisioningError("LIVEKIT_SIP_INBOUND_TRUNK_ID is not set", status=503)
    from livekit import api
    from livekit.protocol.models import ListUpdate

    lk = api.LiveKitAPI(settings.LIVEKIT_URL, settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
    try:
        await lk.sip.update_inbound_trunk_fields(settings.LIVEKIT_SIP_INBOUND_TRUNK_ID,
                                                 numbers=ListUpdate(add=add or [], remove=remove or []))
    finally:
        await lk.aclose()


async def _verify_call(number: str, tenant_id: str) -> Dict[str, Any]:
    """Dial the new number from the outbound trunk and listen for the greeting (Task 3.7's synthetic call)."""
    if not (settings.NUMBER_VERIFY_CALL and settings.LIVEKIT_SIP_OUTBOUND_TRUNK_ID):
        return {"skipped": True, "at": utcnow()}
    from scripts.synthetic_call import one_call

    result = await one_call(tenant_id, timeout=20, hold=0, say=None, dial=number)
    if not result.ok:
        raise ProvisioningError(f"Test call failed: {result.error}", status=502)
    return {"ok": True, "greeting_s": result.greeting_s, "at": utcnow()}


async def run_order(db: Any, order_id: str) -> Dict[str, Any]:
    """Run (or continue) one order. Raises on failure after recording it; the job layer retries."""
    order = await db.number_orders.find_one({"_id": order_id})
    if not order:
        raise ProvisioningError("Order not found", status=404)
    if order["status"] in ("active", "cancelled", "needs_documents"):
        return order
    order.setdefault("steps", {})
    await db.number_orders.update_one({"_id": order_id}, {"$set": {"status": "running", "updated_at": utcnow()},
                                                          "$inc": {"attempts": 1}})
    number, tenant_id, fake = order["e164"], order["tenant_id"], order.get("provider") == "fake"
    step = "start"
    try:
        step = "purchased"
        if not order["steps"].get("purchased"):
            reg = order.get("regulatory") or {}
            sid = "fake" if fake else await _purchase(number, tenant_id, bundle_sid=reg.get("bundle_sid", ""),
                                                      address_sid=reg.get("address_sid", ""))
            await _step(db, order, "purchased", {"sid": sid, "at": utcnow()})
        step = "trunk"
        if not order["steps"].get("trunk"):
            if not fake:
                await _attach_trunk(order["steps"]["purchased"]["sid"])
            await _step(db, order, "trunk", {"at": utcnow()})
        step = "livekit"
        if not order["steps"].get("livekit"):
            if not fake:
                await _livekit_numbers(add=[number])
            await _step(db, order, "livekit", {"at": utcnow()})
        step = "mapped"
        if not order["steps"].get("mapped"):
            await PhoneNumbersStore(db).assign({
                "e164": number, "tenant_id": tenant_id, "purpose": "inbound", "status": "pending",
                "provider": order.get("provider"), "provider_sid": order["steps"]["purchased"]["sid"],
                "country": order.get("country"), "livekit_inbound_trunk_id": settings.LIVEKIT_SIP_INBOUND_TRUNK_ID,
                "order_id": order_id, "assigned_at": utcnow(),
            })
            invalidate(tenant_id)
            await _step(db, order, "mapped", {"at": utcnow()})
        step = "verified"
        if not order["steps"].get("verified"):
            verified = {"skipped": True, "at": utcnow()} if fake else await _verify_call(number, tenant_id)
            await _step(db, order, "verified", verified)
        await db.phone_numbers.update_one({"e164": number}, {"$set": {"status": "active", "activated_at": utcnow()}})
        await db.number_orders.update_one({"_id": order_id}, {"$set": {"status": "active", "error": "",
                                                                       "completed_at": utcnow(), "updated_at": utcnow()}})
        await _make_public_number(db, tenant_id, number)
        logger.info("Number %s is live for %s", mask_phone(number), tenant_id)
    except Exception as exc:
        status = "verify_failed" if step == "verified" else "failed"
        message = str(exc) if isinstance(exc, ProvisioningError) else f"{type(exc).__name__} at step {step}"
        await db.number_orders.update_one({"_id": order_id}, {"$set": {"status": status, "error": message[:300],
                                                                       "failed_step": step, "updated_at": utcnow()}})
        logger.warning("Number order %s failed at %s: %s", order_id, step, message)
        raise
    return await db.number_orders.find_one({"_id": order_id}) or order


async def _make_public_number(db: Any, tenant_id: str, number: str) -> None:
    """A business without a public phone yet reads its AI number to callers (e.g. in texts)."""
    doc = await db.tenants.find_one({"_id": tenant_id}, {"phone": 1})
    if doc is not None and not doc.get("phone"):
        await db.tenants.update_one({"_id": tenant_id}, {"$set": {"phone": number}})
        invalidate(tenant_id)


_BUNDLE_RE = re.compile(r"^BU[0-9a-fA-F]{32}$")
_ADDRESS_RE = re.compile(r"^AD[0-9a-fA-F]{32}$")


async def complete_regulatory(db: Any, order_id: str, *, admin: str, bundle_sid: str = "",
                              address_sid: str = "") -> Dict[str, Any]:
    """Platform admin: Twilio approved the business's regulatory bundle → continue the order as an
    instant one, buying the number with that bundle (and address). Operator-assisted provisioning for
    AU, UK and EU numbers until documents can be uploaded in the app."""
    order = await db.number_orders.find_one({"_id": order_id})
    if not order or order["status"] != "needs_documents":
        raise ProvisioningError("Only orders waiting for documents can be continued", status=409)
    bundle_sid, address_sid = (bundle_sid or "").strip(), (address_sid or "").strip()
    if order.get("provider") == "twilio" and not _BUNDLE_RE.match(bundle_sid):
        raise ProvisioningError("Enter the approved Twilio bundle SID (BU…) for this business", status=422)
    if address_sid and not _ADDRESS_RE.match(address_sid):
        raise ProvisioningError("The address SID must look like AD…", status=422)
    await db.number_orders.update_one({"_id": order_id}, {"$set": {
        "status": "pending", "approved_by": admin, "approved_at": utcnow(),
        "regulatory": {"bundle_sid": bundle_sid, "address_sid": address_sid}}})
    return await db.number_orders.find_one({"_id": order_id}) or order


async def release(db: Any, number: str) -> Dict[str, Any]:
    """Give a number back: LiveKit trunk, Twilio trunk + the number itself, then the routing table.
    Every step tolerates "already gone", so a half-finished release can simply run again."""
    number = e164(number)
    row = await PhoneNumbersStore(db).get(number) or {}
    done: Dict[str, Any] = {}
    provider = row.get("provider") or "twilio"
    if provider == "twilio" and mode() == "twilio":
        if settings.LIVEKIT_SIP_INBOUND_TRUNK_ID:
            try:
                await _livekit_numbers(remove=[number])
                done["livekit"] = True
            except Exception:
                logger.warning("Removing %s from the LiveKit trunk failed", mask_phone(number), exc_info=True)
        sid = row.get("provider_sid") or await _find_owned(number)
        if sid and sid != "fake":
            resp = await _twilio("DELETE", f"{TWILIO_API.format(base=settings.twilio_api_base, sid=settings.TWILIO_ACCOUNT_SID)}"
                                           f"/IncomingPhoneNumbers/{sid}.json")
            if resp.status_code >= 400 and resp.status_code != 404:
                raise ProvisioningError(f"Could not release the number at Twilio (HTTP {resp.status_code})", status=502)
            done["twilio"] = True                  # deleting the number also detaches it from the trunk
    if row:
        await PhoneNumbersStore(db).release(number)
        invalidate(row.get("tenant_id"))
        done["mapping"] = True
    return done


def public_order(order: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in order.items() if k not in ("_id", "documents")}
    out["id"] = order["_id"]
    out["steps"] = {k: bool(v) for k, v in (order.get("steps") or {}).items()}
    return out
