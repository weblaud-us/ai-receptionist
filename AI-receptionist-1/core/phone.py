"""Phone-number helpers: normalisation, matching and masking."""

import re
from typing import Optional

_NON_DIGITS = re.compile(r"\D")


def digits(phone: Optional[str]) -> str:
    """Digits only. Strips 'whatsapp:', '+', spaces, dashes and a leading international '00'."""
    d = _NON_DIGITS.sub("", phone or "")
    if d.startswith("00"):
        d = d[2:]
    return d


def phones_match(a: Optional[str], b: Optional[str]) -> bool:
    """
    True when two phone numbers refer to the same line.

    Exact match on digits, or — when one side is written in national format with a leading
    0 (e.g. 01712345678 vs +8801712345678) — a match on the national significant number.
    """
    da, db = digits(a), digits(b)
    if len(da) < 7 or len(db) < 7:
        return False
    if da == db:
        return True
    for local, intl in ((da, db), (db, da)):
        if local.startswith("0") and not intl.startswith("0"):
            national = local.lstrip("0")
            if len(national) >= 7 and intl.endswith(national):
                return True
    return False


def mask_phone(phone: Optional[str]) -> str:
    """'+8801712345678' -> '+880•••••••5678'. Keeps the country prefix and last 4 digits."""
    if not phone:
        return ""
    plus = "+" if phone.strip().startswith("+") else ""
    d = digits(phone)
    if len(d) <= 4:
        return "•" * len(d)
    head = d[:3] if len(d) > 9 else ""
    return f"{plus}{head}{'•' * (len(d) - len(head) - 4)}{d[-4:]}"
