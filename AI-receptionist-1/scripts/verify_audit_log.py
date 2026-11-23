"""
Check every business's audit-log hash chain (audit PRIV-10). Read-only.

    uv run python -m scripts.verify_audit_log              # every business
    uv run python -m scripts.verify_audit_log ten_abc123   # one

Exit code 1 if any chain is broken: run it from a monitor or after an incident.
"""

from __future__ import annotations

import asyncio
import sys

from core.compliance.audit_chain import verify


async def main() -> None:
    from database.mongodb import connect_db, get_db

    await connect_db(ensure_indexes=False)
    db = get_db()
    ids = sys.argv[1:] or [t["_id"] async for t in db.tenants.find({}, {"_id": 1})]
    broken = 0
    for tid in ids:
        ok, problems = await verify(db, tid)
        print(f"{'OK    ' if ok else 'BROKEN'} {tid}" + ("" if ok else ": " + "; ".join(problems[:5])))
        broken += 0 if ok else 1
    raise SystemExit(1 if broken else 0)


if __name__ == "__main__":
    asyncio.run(main())
