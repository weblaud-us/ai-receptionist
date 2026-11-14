"""
Create a platform admin (you) — the first real user after migration m0003.

    uv run python -m scripts.create_admin --email you@company.com --name "Your Name"

Prompts for the password (never pass it on the command line). Then sign in to the dashboard,
turn on two-factor (Team → Two-factor sign-in), and clear ADMIN_PASSWORD from the environment.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass

from core.log import configure_logging
from database.mongodb import connect_db, disconnect_db


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default="")
    args = parser.parse_args()
    configure_logging("WARNING", "text")
    password = getpass.getpass("Password (10+ characters): ")
    if password != getpass.getpass("Repeat: "):
        raise SystemExit("Passwords don't match")
    await connect_db(ensure_indexes=False)
    try:
        from core.auth.service import create_user

        user = await create_user(email=args.email, password=password, name=args.name, platform_admin=True)
        print(f"Platform admin {user['email']} created ({user['_id']}). Sign in and turn on two-factor.")
    finally:
        await disconnect_db()


if __name__ == "__main__":
    asyncio.run(main())
