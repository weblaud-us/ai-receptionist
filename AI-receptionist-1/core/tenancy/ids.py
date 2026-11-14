"""Prefixed, sortable string IDs: ten_01J…, usr_…, svc_…  (time-ordered like a ULID)."""

import secrets
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32


def _encode(n: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_ALPHABET[n & 31])
        n >>= 5
    return "".join(reversed(out))


def new_id(prefix: str) -> str:
    """`prefix_` + 10 chars of millisecond time + 16 random chars. Sorts by creation time."""
    ts = _encode(int(time.time() * 1000), 10)
    rnd = _encode(secrets.randbits(80), 16)
    return f"{prefix}_{ts}{rnd}"
