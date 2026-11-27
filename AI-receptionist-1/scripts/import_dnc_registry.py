"""
Load a do-not-call registry into the compliance gate (Roadmap Task 4.3).

The gate refuses to dial a number whose country's list is missing, does not cover its area code,
or is older than SALES_DNC_REGISTRY_MAX_AGE_DAYS (31). Run this at least once a month.

    # US National Do Not Call Registry: register as a seller at telemarketing.donotcall.gov (SAN),
    # download the area codes you call, then for each file (or all at once):
    uv run python -m scripts.import_dnc_registry us_national 512.txt 737.txt
    uv run python -m scripts.import_dnc_registry us_national --area-codes 512,737 combined.txt

    # A list that is not split by area code (the whole file replaces the whole list):
    uv run python -m scripts.import_dnc_registry uk_tps tps.txt --country GB --all
    uv run python -m scripts.import_dnc_registry uk_ctps ctps.txt --country GB --all
    uv run python -m scripts.import_dnc_registry ca_dncl dncl.csv --country CA --area-codes 416,647

    # A state list:
    uv run python -m scripts.import_dnc_registry us_state:TX texas.txt --area-codes 512,737

    # Australia's Do Not Call Register can't be downloaded: you submit numbers ("wash") and get a
    # result per number, valid for 30 days. Export the AU numbers that need one (Dialer → Compliance,
    # or GET /platform/sales/dncr-wash.csv), wash them at donotcall.gov.au, then load the result —
    # a CSV with the number and its status ("registered" / "not registered"):
    uv run python -m scripts.import_dnc_registry au_dncr --wash-result wash.csv --country AU

    uv run python -m scripts.import_dnc_registry --status        # what is loaded, and what is stale

Input: one number per line, or CSV with the number in the first column. US files are usually
10 digits ("5125550199") or "512,5550199"; anything `phonenumbers` can read in --country works.
Lines that are not a valid number are counted and skipped.

For a US file with no --area-codes, the area codes are taken from the numbers in it. An area code
you downloaded that has NO registered numbers must be named with --area-codes, or the gate will
still treat it as not covered.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from typing import Any, Dict, Iterator, List, Set

from core.outbound import phones, registry
from database.mongodb import connect_db, disconnect_db, get_db

_SPLIT = re.compile(r"[,;\t ]+")


def parse_line(line: str, country: str) -> str:
    """The E.164 number on one line of a registry file, or "" when there is none."""
    text = line.strip()
    if not text or text.startswith("#"):
        return ""
    parts = [p for p in _SPLIT.split(text) if p]
    # "512,5550199" (area code and number in two columns), or the whole number in column one
    candidates = [parts[0] + parts[1]] if len(parts) >= 2 and len(parts[0]) == 3 and parts[1].isdigit() else []
    candidates.append(parts[0])
    for candidate in candidates:
        info = phones.parse(candidate, country)
        if info is not None:
            return info.e164
    return ""


def read_numbers(paths: List[str], country: str, stats: Dict[str, Any]) -> Iterator[str]:
    for path in paths:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                number = parse_line(line, country)
                if not number:
                    stats["invalid"] += 1
                    continue
                stats["codes"].add(phones.area_code(number))
                yield number


def read_wash(path: str, country: str) -> tuple[List[str], List[str], int]:
    """A wash result: number, then a status column. "registered" (or "yes"/"y"/"1") = on the register."""
    washed: List[str] = []
    registered: List[str] = []
    invalid = 0
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            parts = [p.strip().strip('"') for p in re.split(r"[,;\t]", line.strip()) if p.strip()]
            if not parts or parts[0].lower() in ("number", "phone", "telephone number"):
                continue
            info = phones.parse(parts[0], country)
            if info is None:
                invalid += 1
                continue
            status = (parts[1] if len(parts) > 1 else "").lower()
            (registered if status in ("registered", "yes", "y", "1", "true") else washed).append(info.e164)
    return washed, registered, invalid


async def run(args: argparse.Namespace) -> int:
    await connect_db(ensure_indexes=False)
    try:
        db = get_db()
        if args.status:
            rows = await registry.registry_overview(db)
            if not rows:
                print("No do-not-call registry is loaded. US numbers cannot be dialled until us_national is.")
            for r in rows:
                stale = f"   STALE: {', '.join(r['stale_area_codes'])}" if r["stale_area_codes"] else ""
                print(f"{r['list']:<16} {r['rows']:>10} numbers   refreshed {r['refreshed_at']:%Y-%m-%d}   "
                      f"area codes: {', '.join(r['area_codes'])}{stale}")
            return 0
        if args.wash_result:
            washed, registered, invalid = read_wash(args.wash_result, args.country.upper())
            result = await registry.record_wash(db, args.list or "au_dncr", washed, registered)
            print(f"{args.list or 'au_dncr'}: {result['washed']} numbers washed, {result['registered']} registered "
                  f"({invalid} unreadable lines skipped)")
            return 0
        if not args.list or not args.files:
            print("usage: import_dnc_registry <list> <file>... [--area-codes 512,737 | --all]", file=sys.stderr)
            return 2
        stats: Dict[str, Any] = {"invalid": 0, "codes": set()}
        numbers = list(read_numbers(args.files, args.country.upper(), stats))
        found: Set[str] = {c for c in stats["codes"] if c}
        if args.all:
            codes = [registry.ALL_AREA_CODES]
        elif args.area_codes:
            codes = [c.strip() for c in args.area_codes.split(",") if c.strip()]
            outside = found - set(codes)
            if outside:
                print(f"The file has numbers in area codes not named in --area-codes: {', '.join(sorted(outside))}",
                      file=sys.stderr)
                return 2
        else:
            codes = sorted(found)
        if not codes:
            print("Nothing to load: no valid numbers and no --area-codes / --all.", file=sys.stderr)
            return 2
        count = await registry.replace_registry(db, args.list, numbers, area_codes=codes)
        print(f"{args.list}: loaded {count} numbers for {', '.join(codes)} ({stats['invalid']} unreadable lines skipped)")
        return 0
    finally:
        await disconnect_db()


def main() -> None:
    p = argparse.ArgumentParser(description="Load a do-not-call registry for the outbound dialer's gate")
    p.add_argument("list", nargs="?", help="us_national | us_state:XX | ca_dncl | uk_tps | uk_ctps | au_dncr")
    p.add_argument("files", nargs="*", help="files with one number per line")
    p.add_argument("--country", default="US", help="country the numbers are written in (default US)")
    p.add_argument("--area-codes", help="comma-separated area codes this download covers")
    p.add_argument("--all", action="store_true", help="the file is the complete list (not split by area code)")
    p.add_argument("--status", action="store_true", help="show what is loaded")
    p.add_argument("--wash-result", help="au_dncr: a wash result CSV (number, registered / not registered)")
    raise SystemExit(asyncio.run(run(p.parse_args())))


if __name__ == "__main__":
    main()
