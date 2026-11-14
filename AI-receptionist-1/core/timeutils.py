"""
Business-local time.

Rule: store timestamps in UTC; convert to the business's timezone only at the edges
(prompt text, messages to guests, dashboard display). Date-only concepts such as a hotel
night stay as local "YYYY-MM-DD" strings.
"""

from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _zone(tz: Optional[str]) -> ZoneInfo:
    try:
        return ZoneInfo(tz or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def business_now(tz: Optional[str], now_utc: Optional[datetime] = None) -> datetime:
    """Current wall-clock time in the business's timezone."""
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(_zone(tz))


def business_today(tz: Optional[str], now_utc: Optional[datetime] = None) -> date:
    """Today's date where the business is — never the server's local date."""
    return business_now(tz, now_utc).date()
