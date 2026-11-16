"""
Create the LiveKit SIP objects for the active telephony provider (idempotent).

    uv run python -m scripts.setup_telephony --dry-run                 # show what would change
    uv run python -m scripts.setup_telephony                           # create / update
    uv run python -m scripts.setup_telephony --force                   # also re-send passwords (after changing one)
    uv run python -m scripts.setup_telephony --assign TENANT_ID        # softphone: also route the FIRST
                                                                       # fake number to that business
    uv run python -m scripts.setup_telephony --assign TENANT_ID --number +15550000002   # a specific one

Numbers you don't assign stay unassigned, which is how you test the "unassigned number" path.

Which provider is used comes from APP_ENV (config.py `telephony_provider`):

    development         softphone   Linphone → LiveKit SIP. Free, no Twilio account.
    staging/production  twilio      Twilio Elastic SIP trunk → LiveKit SIP.
    TELEPHONY_PROVIDER=livekit      a US number rented in LiveKit Cloud (Telephony → Phone Numbers):
                                    no Twilio, no trunk. This script only creates the dispatch rule
                                    that you then assign to the number.

Objects are found by name, so running this twice changes nothing the second time:
    inbound trunk    {provider}-inbound-{APP_ENV}
    dispatch rule    receptionist-{APP_ENV}     (scoped to that inbound trunk)
                     receptionist-{APP_ENV}-number  (livekit: not tied to a trunk; assigned to the number)
    outbound trunk   twilio-outbound-{APP_ENV}  (twilio only: transfers and outbound calls)

The Twilio Console steps (buy number, create trunk, origination URI, credentials) stay manual:
see docs/TELEPHONY_TESTING.md.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Any, Optional, Sequence

from livekit import api
from livekit.protocol import sip as proto_sip
from livekit.protocol.agent_dispatch import RoomAgentDispatch
from livekit.protocol.room import RoomConfiguration

from config import Settings, settings
from core.log import configure_logging
from core.phone import digits

logger = logging.getLogger("scripts.setup_telephony")

ROOM_PREFIX = "call-"   # must match what agent/worker.py and the dashboards expect for phone calls


# ── pure builders (unit-tested, no network) ─────────────────────────────────────────────────

def _e164(raw: str) -> str:
    d = digits(raw)
    return f"+{d}" if d else ""


def normalize_numbers(raw: Sequence[str]) -> list[str]:
    """E.164, de-duplicated, order kept."""
    seen: dict[str, None] = {}
    for n in raw:
        e = _e164(n)
        if e:
            seen.setdefault(e, None)
    return list(seen)


def inbound_trunk_name(s: Settings) -> str:
    return f"{s.telephony_provider}-inbound-{s.APP_ENV}"


def dispatch_rule_name(s: Settings) -> str:
    suffix = "-number" if s.telephony_provider == "livekit" else ""
    return f"receptionist-{s.APP_ENV}{suffix}"


def outbound_trunk_name(s: Settings) -> str:
    return f"twilio-outbound-{s.APP_ENV}"


def build_inbound_trunk(s: Settings, numbers: Sequence[str]) -> proto_sip.SIPInboundTrunkInfo:
    """The trunk LiveKit uses to accept calls to `numbers`."""
    if s.telephony_provider == "softphone":
        username, password = s.SOFTPHONE_SIP_USERNAME, s.SOFTPHONE_SIP_PASSWORD
    else:
        username, password = s.TWILIO_SIP_INBOUND_USERNAME, s.TWILIO_SIP_INBOUND_PASSWORD
    return proto_sip.SIPInboundTrunkInfo(
        name=inbound_trunk_name(s),
        numbers=normalize_numbers(numbers),
        auth_username=username,
        auth_password=password,
        krisp_enabled=True,
    )


def build_dispatch_rule(s: Settings, trunk_ids: Sequence[str]) -> proto_sip.SIPDispatchRuleInfo:
    """One room per call (`call-…`), with the receptionist agent dispatched into it."""
    return proto_sip.SIPDispatchRuleInfo(
        name=dispatch_rule_name(s),
        trunk_ids=list(trunk_ids),
        rule=proto_sip.SIPDispatchRule(
            dispatch_rule_individual=proto_sip.SIPDispatchRuleIndividual(room_prefix=ROOM_PREFIX)),
        room_config=RoomConfiguration(agents=[RoomAgentDispatch(agent_name=s.LIVEKIT_AGENT_NAME)]),
    )


def build_outbound_trunk(s: Settings, numbers: Sequence[str]) -> proto_sip.SIPOutboundTrunkInfo:
    """Calls going out through Twilio: `transfer_to_human` and the Phase 4 dialer."""
    return proto_sip.SIPOutboundTrunkInfo(
        name=outbound_trunk_name(s),
        address=s.TWILIO_TERMINATION_URI,
        numbers=normalize_numbers(numbers),
        auth_username=s.TWILIO_SIP_OUTBOUND_USERNAME,
        auth_password=s.TWILIO_SIP_OUTBOUND_PASSWORD,
    )


def sip_host_from_livekit_url(url: str) -> Optional[str]:
    """wss://abc123.livekit.cloud → abc123.sip.livekit.cloud (LiveKit Cloud). None if it isn't Cloud."""
    host = url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    suffix = ".livekit.cloud"
    if host.endswith(suffix) and ".sip." not in host:
        return f"{host[: -len(suffix)]}.sip.livekit.cloud"
    return None


def problems(s: Settings) -> list[str]:
    """Missing settings for the active provider. Empty list = good to go."""
    out = [f"{v} is not set" for v in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
           if not getattr(s, v)]
    if s.telephony_provider == "softphone":
        if s.is_production:
            out.append("softphone mode is for development only (APP_ENV=production)")
        if not s.softphone_numbers:
            out.append("SOFTPHONE_TEST_NUMBERS is empty")
        if not (s.SOFTPHONE_SIP_USERNAME and s.SOFTPHONE_SIP_PASSWORD):
            out.append("SOFTPHONE_SIP_USERNAME and SOFTPHONE_SIP_PASSWORD are required "
                       "(an open trunk would let anyone who finds the SIP host call your agent)")
    return out


# ── idempotence helpers ─────────────────────────────────────────────────────────────────────

# `media` is an empty block LiveKit adds on its own; we never set it.
_SERVER_FIELDS = ("sip_trunk_id", "sip_dispatch_rule_id", "created_at", "updated_at", "media")
# LiveKit never returns stored passwords, so they can't be compared: change one, then run with --force.
_WRITE_ONLY_FIELDS = ("auth_password",)


def same(existing: Any, desired: Any) -> bool:
    """Protobuf equality ignoring ids, timestamps and write-only secrets."""
    a, b = type(existing)(), type(desired)()
    a.CopyFrom(existing)
    b.CopyFrom(desired)
    for m in (a, b):
        for f in _SERVER_FIELDS + _WRITE_ONLY_FIELDS:
            if f in m.DESCRIPTOR.fields_by_name:
                m.ClearField(f)
    return a == b


async def _twilio_numbers() -> list[str]:
    """Active Twilio numbers from the routing table, plus the legacy TWILIO_PHONE_NUMBER."""
    nums = [settings.TWILIO_PHONE_NUMBER]
    try:
        from core.repositories.repos import PhoneNumbersStore
        from database.mongodb import connect_db, disconnect_db, get_db

        await connect_db(ensure_indexes=False)
        try:
            rows = await PhoneNumbersStore(get_db()).list(limit=5000)
        finally:
            await disconnect_db()
        nums += [r["e164"] for r in rows if r.get("provider") == "twilio" and r.get("status") != "released"]
    except Exception as e:  # DB down: fall back to the env number rather than fail
        logger.warning("Could not read phone_numbers (%s); using TWILIO_PHONE_NUMBER only", e)
    return nums


async def _assign_numbers(tenant_id: str, numbers: Sequence[str], dry_run: bool, provider: str = "softphone") -> None:
    """Route softphone numbers to a business (same effect as POST /platform/phone-numbers)."""
    from core.repositories.base import utcnow
    from core.repositories.repos import PhoneNumbersStore, TenantsStore
    from core.tenancy import invalidate
    from database.mongodb import connect_db, disconnect_db, get_db

    await connect_db(ensure_indexes=False)
    try:
        if not await TenantsStore(get_db()).get(tenant_id):
            raise SystemExit(f"Tenant {tenant_id} not found. List them: GET /platform/tenants")
        store = PhoneNumbersStore(get_db())
        for e164 in numbers:
            existing = await store.get(e164)
            if existing and existing.get("status") != "released" and existing.get("tenant_id") != tenant_id:
                raise SystemExit(f"{e164} already belongs to another business. Release it first.")
            if dry_run:
                print(f"  would assign {e164} → tenant {tenant_id}")
                continue
            await store.assign({"e164": e164, "tenant_id": tenant_id, "purpose": "inbound",
                                "provider": provider, "status": "active", "assigned_at": utcnow()})
            print(f"  assigned {e164} → tenant {tenant_id}")
        invalidate()
    finally:
        await disconnect_db()
    if not dry_run:
        print("  (a running worker caches tenant lookups for up to 60 s)")


# ── LiveKit calls ───────────────────────────────────────────────────────────────────────────

async def _upsert_inbound(lk: api.LiveKitAPI, desired: proto_sip.SIPInboundTrunkInfo, dry_run: bool,
                          force: bool = False) -> str:
    found = (await lk.sip.list_inbound_trunk(proto_sip.ListSIPInboundTrunkRequest())).items
    current = next((t for t in found if t.name == desired.name), None)
    if current is None:
        print(f"  inbound trunk  {desired.name}: {'would create' if dry_run else 'creating'} "
              f"(numbers {list(desired.numbers)})")
        if dry_run:
            return "<new-trunk>"
        made = await lk.sip.create_inbound_trunk(proto_sip.CreateSIPInboundTrunkRequest(trunk=desired))
        return made.sip_trunk_id
    if same(current, desired) and not force:
        print(f"  inbound trunk  {desired.name}: up to date ({current.sip_trunk_id})")
    else:
        print(f"  inbound trunk  {desired.name}: {'would update' if dry_run else 'updating'} ({current.sip_trunk_id})")
        if not dry_run:
            await lk.sip.update_inbound_trunk(current.sip_trunk_id, desired)
    return current.sip_trunk_id


async def _upsert_dispatch(lk: api.LiveKitAPI, s: Settings, trunk_ids: Sequence[str], dry_run: bool) -> None:
    desired = build_dispatch_rule(s, trunk_ids)
    found = (await lk.sip.list_dispatch_rule(proto_sip.ListSIPDispatchRuleRequest())).items
    current = next((r for r in found if r.name == desired.name), None)
    if current is None:
        print(f"  dispatch rule  {desired.name}: {'would create' if dry_run else 'creating'} "
              f"(rooms '{ROOM_PREFIX}…', agent '{s.LIVEKIT_AGENT_NAME}')")
        if not dry_run:
            await lk.sip.create_dispatch_rule(proto_sip.CreateSIPDispatchRuleRequest(dispatch_rule=desired))
    elif same(current, desired):
        print(f"  dispatch rule  {desired.name}: up to date ({current.sip_dispatch_rule_id})")
    else:
        print(f"  dispatch rule  {desired.name}: {'would update' if dry_run else 'updating'} "
              f"({current.sip_dispatch_rule_id})")
        if not dry_run:
            await lk.sip.update_dispatch_rule(current.sip_dispatch_rule_id, desired)


async def _upsert_outbound(lk: api.LiveKitAPI, desired: proto_sip.SIPOutboundTrunkInfo, dry_run: bool,
                           force: bool = False) -> Optional[str]:
    found = (await lk.sip.list_outbound_trunk(proto_sip.ListSIPOutboundTrunkRequest())).items
    current = next((t for t in found if t.name == desired.name), None)
    if current is None:
        print(f"  outbound trunk {desired.name}: {'would create' if dry_run else 'creating'} → {desired.address}")
        if dry_run:
            return None
        made = await lk.sip.create_outbound_trunk(proto_sip.CreateSIPOutboundTrunkRequest(trunk=desired))
        return made.sip_trunk_id
    if same(current, desired) and not force:
        print(f"  outbound trunk {desired.name}: up to date ({current.sip_trunk_id})")
    else:
        print(f"  outbound trunk {desired.name}: {'would update' if dry_run else 'updating'} ({current.sip_trunk_id})")
        if not dry_run:
            await lk.sip.update_outbound_trunk(current.sip_trunk_id, desired)
    return current.sip_trunk_id


def _print_softphone_help(s: Settings) -> None:
    host = sip_host_from_livekit_url(s.LIVEKIT_URL)
    print("\nLinphone account (Preferences → Accounts → Use a SIP account):")
    print(f"  Username        {s.SOFTPHONE_SIP_USERNAME}")
    print("  Password        SOFTPHONE_SIP_PASSWORD from your .env")
    print(f"  Domain          {host or '<copy from LiveKit Cloud → Settings → SIP URI>'}")
    if host:
        print("                  (derived from LIVEKIT_URL; confirm it matches Settings → SIP URI)")
    print("  Transport       TCP")
    print("  Registration    OFF (LiveKit trunks accept calls but not REGISTER)")
    print(f"\nDial one of: {', '.join(s.softphone_numbers)}")
    print(f"  as  sip:{s.softphone_numbers[0]}@{host or '<sip-host>'}")


# ── main ────────────────────────────────────────────────────────────────────────────────────

def _print_livekit_help(s: Settings) -> None:
    print("\nNext, in LiveKit Cloud (cloud.livekit.io → your project):")
    print("  1. Telephony → Phone Numbers → Rent a number (a US local number; your first one is free)")
    print(f"  2. On that number open ⋮ → Assign dispatch rule → choose '{dispatch_rule_name(s)}' → Save")
    print("  3. Call the number from any phone. Watch the worker log for 'SIP participant attributes'")
    print("  Business: with no businesses created yet the .env hotel answers; otherwise route the number with")
    print("  uv run python -m scripts.setup_telephony --assign TENANT_ID --number +1XXXXXXXXXX")


async def run(dry_run: bool, assign: Optional[str], number: Optional[str] = None, force: bool = False) -> None:
    s = settings
    provider = s.telephony_provider
    print(f"APP_ENV={s.APP_ENV}  telephony={provider}{'  (dry run)' if dry_run else ''}")

    issues = problems(s)
    if issues:
        raise SystemExit("Fix these first:\n  - " + "\n  - ".join(issues))
    if (assign or number) and provider == "twilio":
        raise SystemExit("--assign is for softphone and livekit modes. In twilio mode assign numbers with "
                         "POST /platform/phone-numbers (or the dashboard).")
    if number and not assign:
        raise SystemExit("--number only makes sense together with --assign TENANT_ID")

    if provider == "livekit":      # a number rented in LiveKit Cloud: no trunk, just the dispatch rule
        if assign and not number:
            raise SystemExit("With the livekit provider, say which number: --assign TENANT_ID --number +1XXXXXXXXXX")
        async with api.LiveKitAPI(s.LIVEKIT_URL, s.LIVEKIT_API_KEY, s.LIVEKIT_API_SECRET) as lk:
            await _upsert_dispatch(lk, s, [], dry_run)
        if assign and number:
            print("\nRouting:")
            await _assign_numbers(assign, normalize_numbers([number]), dry_run, "livekit")
        _print_livekit_help(s)
        return

    if provider == "softphone":
        numbers = s.softphone_numbers
    else:
        numbers = await _twilio_numbers()
    numbers = normalize_numbers(numbers)
    if not numbers:
        raise SystemExit("No phone numbers to put on the trunk. "
                         "Set TWILIO_PHONE_NUMBER or assign a number to a business first.")

    target = normalize_numbers([number]) if number else numbers[:1]   # which number --assign routes
    if assign and (not target or target[0] not in numbers):
        raise SystemExit(f"{number} is not one of SOFTPHONE_TEST_NUMBERS ({', '.join(numbers)})")

    inbound = build_inbound_trunk(s, numbers)
    if provider == "twilio" and not inbound.auth_username and not inbound.allowed_addresses:
        print("  ⚠ inbound trunk has no credentials and no allowed addresses: anyone who finds the SIP "
              "host could call these numbers.\n    Set TWILIO_SIP_INBOUND_USERNAME/PASSWORD "
              "(and put them on the Twilio origination URI), or restrict by IP in LiveKit.")

    async with api.LiveKitAPI(s.LIVEKIT_URL, s.LIVEKIT_API_KEY, s.LIVEKIT_API_SECRET) as lk:
        trunk_id = await _upsert_inbound(lk, inbound, dry_run, force)
        await _upsert_dispatch(lk, s, [trunk_id], dry_run)
        if provider == "twilio":
            if s.TWILIO_TERMINATION_URI and s.TWILIO_SIP_OUTBOUND_USERNAME and s.TWILIO_SIP_OUTBOUND_PASSWORD:
                out_id = await _upsert_outbound(lk, build_outbound_trunk(s, numbers), dry_run, force)
                if out_id:
                    note = "" if out_id == s.LIVEKIT_SIP_OUTBOUND_TRUNK_ID else "   ← set this in your env"
                    print(f"\nLIVEKIT_SIP_OUTBOUND_TRUNK_ID={out_id}{note}")
            else:
                print("  outbound trunk skipped: set TWILIO_TERMINATION_URI and TWILIO_SIP_OUTBOUND_USERNAME/"
                      "PASSWORD to enable transfers and outbound calls")

    if assign:
        print("\nRouting:")
        await _assign_numbers(assign, target, dry_run)
    if provider == "softphone":
        _print_softphone_help(s)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    parser.add_argument("--assign", metavar="TENANT_ID",
                        help="softphone only: route a fake number to this business (default: the first one)")
    parser.add_argument("--number", metavar="E164",
                        help="with --assign: which of SOFTPHONE_TEST_NUMBERS to route")
    parser.add_argument("--force", action="store_true",
                        help="re-send the trunks even if nothing looks changed (use after changing a SIP password: "
                             "LiveKit never shows stored passwords, so they can't be compared)")
    args = parser.parse_args()
    configure_logging("WARNING", "text")
    asyncio.run(run(args.dry_run, args.assign, args.number, args.force))


if __name__ == "__main__":
    main()
