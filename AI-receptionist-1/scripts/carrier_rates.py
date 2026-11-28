"""
Phone-network rates from Twilio's Trunking Pricing API (account-specific), for checking and for keeping
the fallback table in core/billing/costs.py honest.

    uv run python -m scripts.carrier_rates --countries US,CA,AU,GB     # refresh the cache, print the rates
    uv run python -m scripts.carrier_rates --number +61412345678       # what one call there would cost
    uv run python -m scripts.carrier_rates --countries AU --snippet    # print a FALLBACK_CARRIER entry to paste

Needs TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN and MongoDB (the rates are cached in `carrier_rates`).
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import phonenumbers

from core.billing import carrier_rates as cr
from database.mongodb import connect_db, disconnect_db, get_db


def _example(iso: str, kind) -> str:
    return cr._example(iso, kind)


async def _country(db, iso: str, snippet: bool) -> None:
    doc = await cr.refresh(db, iso)
    if not doc:
        print(f"{iso}: no rates (check the Twilio credentials, or Twilio has no trunking prices there)")
        return
    T = phonenumbers.PhoneNumberType
    landline, mobile = _example(iso, T.FIXED_LINE), _example(iso, T.MOBILE)
    land = cr.match_termination(doc, cr._digits(landline)) if landline else None
    mob = cr.match_termination(doc, cr._digits(mobile)) if mobile else None
    orig = doc.get("originating") or {}
    print(f"{iso} ({doc.get('country')}): {len(doc.get('terminating') or [])} termination routes")
    print(f"  inbound  local {orig.get('local')}  mobile {orig.get('mobile')}  toll-free {orig.get('toll_free')}")
    print(f"  outbound landline {land and land['usd']} ({land and land['name']})")
    print(f"  outbound mobile   {mob and mob['usd']} ({mob and mob['name']})")
    if snippet:
        inbound = {"local": orig.get("local"), "mobile": orig.get("mobile", orig.get("local")),
                   "toll_free": orig.get("toll_free", orig.get("local"))}
        out = {"landline": land and land["usd"], "mobile": mob and mob["usd"]}
        print(f'    "{iso}": {{"in": {inbound}, "out": {out},\n'
              f'           "basis": "Twilio Elastic SIP, {doc.get("country")} (account rate, {doc["fetched_at"]:%b %Y})"}},')


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--countries", default="", help="comma-separated ISO codes, e.g. US,AU,GB")
    parser.add_argument("--number", default="", help="an E.164 number to price, inbound and outbound")
    parser.add_argument("--from", dest="from_number", default="", help="caller ID for the outbound estimate")
    parser.add_argument("--snippet", action="store_true", help="print FALLBACK_CARRIER entries to paste")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")       # Windows consoles default to cp1252
    if not cr.configured():
        print("Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN first: without them only the fallback table answers.")
    await connect_db(ensure_indexes=False)
    try:
        db = get_db()
        for iso in [c.strip().upper() for c in args.countries.split(",") if c.strip()]:
            await _country(db, iso, args.snippet)
        if args.number:
            for direction in ("outbound", "inbound"):
                est = await cr.estimate(db, args.number, direction=direction, from_number=args.from_number)
                print(f"{direction:8} {est['country_name']} {est['line_text']}: carrier ${est['carrier_usd_per_min']}/min, "
                      f"all-in ${est['per_minute']['mid']}/min, {est['typical_minutes']:g}-min call ≈ "
                      f"${est['typical_call_usd']} [{est['source']}: {est['basis']}]")
    finally:
        await disconnect_db()


if __name__ == "__main__":
    asyncio.run(main())
