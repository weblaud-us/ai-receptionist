"""
Record a "stop contacting me" that reached you outside the product (e-mail, letter, phone call to you).

    uv run python -m scripts.opt_out +15125550111 --channel email --note "e-mail of 19 Nov"
    uv run python -m scripts.opt_out +15125550111 --tenant ten_abc   # one business only

Without --tenant it is platform-wide: every business stops texting the number and it goes on the
permanent sales do-not-call list (core/compliance/optout.py). Logged in consent_events.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Optional

from config import settings
from core.log import configure_logging


async def main(phone: str, channel: str, tenant: Optional[str], note: str) -> None:
    from core.compliance.optout import record_opt_out
    from database.mongodb import connect_db, disconnect_db, get_db

    configure_logging(settings.LOG_LEVEL, "text")
    await connect_db(ensure_indexes=False)
    try:
        result = await record_opt_out(get_db(), phone, channel=channel, source="script", tenant_id=tenant, note=note)
        print(json.dumps(result, indent=2, default=str))
    finally:
        await disconnect_db()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Record an opt-out received outside the product.")
    parser.add_argument("phone", help="the number, with country code")
    parser.add_argument("--channel", default="email", help="how it reached you: email, letter, phone…")
    parser.add_argument("--tenant", default=None, help="limit to one business (tenant id)")
    parser.add_argument("--note", default="", help="where the request is filed")
    args = parser.parse_args()
    asyncio.run(main(args.phone, args.channel, args.tenant, args.note))
